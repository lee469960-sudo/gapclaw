"""Advisory Agent capability routing for bound MCPs and Skills.

This module deliberately works from persisted metadata only.  It never reads
Skill.md, connects to MCP endpoints, calls tools/list, or grants permissions.
The existing ReAct/MCP/Skill execution paths remain authoritative.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


ROUTE_NORMAL_CHAT = "normal_chat"
ROUTE_CAPABILITY_CANDIDATE = "capability_candidate"
ROUTE_EXPLICIT_CAPABILITY_REQUEST = "explicit_capability_request"
ROUTE_MISSING_CAPABILITY = "missing_capability"
ROUTE_BLOCKED_CAPABILITY = "blocked_capability"

_MAX_HINT_ITEMS = 30
_MAX_HINT_LEN = 64
_MAX_DESCRIPTION_LEN = 1000
_MAX_MCP_CANDIDATES = 5
_MAX_SKILL_CANDIDATES = 3
_CREDENTIAL_RE = re.compile(
    r"(?i)(authorization|bearer|token|api[_-]?key|secret|password)\s*[:=]\s*[^,\s]+"
)
_EN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9._-]{1,}")
_ZH_TOKEN_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_ACTION_INTENT_RE = re.compile(
    r"(?:帮我|请|想要|需要|看看|查看|查询|获取|列出|显示|检查|读取|使用|调用|通过|导出|分析|优化|"
    r"选股|筛选|筛选出|过滤|选出|查一下|看一下|show|check|fetch|list|use|call|query|export|analy[sz]e)",
    re.IGNORECASE,
)
_STRUCTURED_INTENT_RE = re.compile(
    r"(?:条件(?:如下|是|为)?\s*[:：]|基准日|至少|不选|筛选条件|criteria|where\b|\n\s*\d+[.、)])",
    re.IGNORECASE,
)
_KNOWLEDGE_RE = re.compile(r"(?:什么是|是什么|解释|介绍|定义|含义|原理|区别|为什么)", re.IGNORECASE)
_STOPWORDS = {
    "当前", "现在", "信息", "情况", "内容", "数据", "查询", "查看", "看看", "帮我",
    "请问", "一下", "这个", "那个", "the", "current", "please", "query", "show",
}


@dataclass(frozen=True)
class CapabilityProfile:
    type: str
    id: str
    name: str
    tags: list[str] = field(default_factory=list)
    description: str = ""
    keywords: list[str] = field(default_factory=list)
    aliases: list[str] = field(default_factory=list)
    enabled: bool = True
    allowed: bool = True

    def classifier_hint(self) -> dict[str, str]:
        merged_tags = _dedupe([*self.tags, *self.keywords, *self.aliases])
        return {
            "id": self.id,
            "name": self.name,
            "tags": ", ".join(merged_tags),
            "description": self.description,
            "type": self.type,
        }


@dataclass(frozen=True)
class CapabilityCandidate:
    type: str
    id: str
    name: str
    score: int
    hit_terms: list[str]
    reason: str
    description: str = ""

    def to_context_dict(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "id": self.id,
            "name": self.name,
            "score": self.score,
            "hit_terms": self.hit_terms[:8],
            "reason": self.reason,
            "description": self.description[:300],
        }


@dataclass(frozen=True)
class CapabilityRouteResult:
    mode: str = ROUTE_NORMAL_CHAT
    candidates: list[CapabilityCandidate] = field(default_factory=list)
    blocked: list[dict[str, str]] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def selected_ids(self) -> list[str]:
        return [item.id for item in self.candidates]

    def to_trace_dict(self, *, mcp_route: dict[str, Any] | None = None) -> dict[str, Any]:
        trace = {
            "route_mode": self.mode,
            "candidate_count": len(self.candidates),
            "candidate_ids": [
                {"type": item.type, "id": item.id, "score": item.score}
                for item in self.candidates[:10]
            ],
            "matched_terms": _dedupe(term for item in self.candidates for term in item.hit_terms)[:20],
            "blocked": self.blocked[:10],
            "missing": self.missing[:10],
        }
        if self.error:
            trace["error"] = self.error[:80]
        if mcp_route:
            trace["mcp_route"] = mcp_route
        return trace


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _split_terms(value: Any) -> list[str]:
    if value is None:
        return []
    raw: list[Any]
    if isinstance(value, str):
        raw = re.split(r"[,，;\n]+", value)
    elif isinstance(value, (list, tuple, set)):
        raw = list(value)
    else:
        raw = [value]
    out: list[str] = []
    for item in raw:
        text = re.sub(r"\s+", " ", str(item or "")).strip()
        if not text:
            continue
        out.append(text[:_MAX_HINT_LEN])
        if len(out) >= _MAX_HINT_ITEMS:
            break
    return _dedupe(out)


def _dedupe(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def sanitize_routing_hints(value: Any) -> dict[str, Any]:
    """Normalize user-provided routing hints without changing permissions."""
    raw = _json_dict(value)
    hints: dict[str, Any] = {}
    if "enabled" in raw:
        hints["enabled"] = bool(raw.get("enabled"))
    for key in ("keywords", "aliases", "tags"):
        items = _split_terms(raw.get(key))
        if items:
            hints[key] = items
    description = re.sub(r"\s+", " ", str(raw.get("description") or "")).strip()
    if description:
        hints["description"] = description[:_MAX_DESCRIPTION_LEN]
    return hints


def sanitize_capability_routing_overrides(value: Any) -> dict[str, Any]:
    """Normalize Agent binding-level overrides keyed by capability id."""
    raw = _json_dict(value)
    out: dict[str, Any] = {}
    for cap_id, override in raw.items():
        key = str(cap_id or "").strip()
        if not key:
            continue
        hints = sanitize_routing_hints(override)
        if hints:
            out[key[:128]] = hints
    return out


def _merge_hints(global_hints: Any, binding_override: Any = None) -> dict[str, Any]:
    global_clean = sanitize_routing_hints(global_hints)
    override_clean = sanitize_routing_hints(binding_override)
    enabled = override_clean.get("enabled", global_clean.get("enabled", True))
    description = override_clean.get("description") or global_clean.get("description") or ""
    return {
        "enabled": bool(enabled),
        "keywords": _dedupe([*global_clean.get("keywords", []), *override_clean.get("keywords", [])]),
        "aliases": _dedupe([*global_clean.get("aliases", []), *override_clean.get("aliases", [])]),
        "tags": _dedupe([*global_clean.get("tags", []), *override_clean.get("tags", [])]),
        "description": str(description or "")[:_MAX_DESCRIPTION_LEN],
    }


def _capability_overrides(agent: Any) -> dict[str, Any]:
    return sanitize_capability_routing_overrides(getattr(agent, "capability_routing", "{}"))


def _profile_from_model(model: Any, *, cap_type: str, allowed: bool, override: Any = None) -> CapabilityProfile:
    hints = _merge_hints(getattr(model, "routing", "{}"), override)
    base_tags = _split_terms(getattr(model, "tags", ""))
    description = hints.get("description") or str(getattr(model, "description", "") or "").strip()
    return CapabilityProfile(
        type=cap_type,
        id=str(getattr(model, "id", "") or "").strip(),
        name=str(getattr(model, "name", "") or getattr(model, "id", "") or "").strip(),
        tags=_dedupe([*base_tags, *hints.get("tags", [])]),
        description=description[:_MAX_DESCRIPTION_LEN],
        keywords=list(hints.get("keywords", [])),
        aliases=list(hints.get("aliases", [])),
        enabled=bool(hints.get("enabled", True)),
        allowed=bool(allowed),
    )


def build_bound_capability_profiles(
    db: Any,
    agent: Any,
    *,
    mcp_ids: list[str] | None = None,
    skill_ids: list[str] | None = None,
    allowed_actions: list[str] | None = None,
) -> list[CapabilityProfile]:
    """Read bound MCP/Skill metadata without MCP I/O or Skill.md reads."""
    if not hasattr(db, "query"):
        return []
    allowed_set = set(allowed_actions or [])
    overrides = _capability_overrides(agent)
    profiles: list[CapabilityProfile] = []
    seen: set[tuple[str, str]] = set()
    try:
        from app.models import MCP, Skill

        for raw_id in mcp_ids or []:
            cap_id = str(raw_id or "").strip()
            if not cap_id or ("mcp", cap_id) in seen:
                continue
            seen.add(("mcp", cap_id))
            mcp = db.query(MCP).filter(MCP.id == cap_id).first()
            if mcp:
                profiles.append(_profile_from_model(
                    mcp,
                    cap_type="mcp",
                    allowed="mcp_tool_call" in allowed_set,
                    override=overrides.get(cap_id),
                ))
        for raw_id in skill_ids or []:
            cap_id = str(raw_id or "").strip()
            if not cap_id or ("skill", cap_id) in seen:
                continue
            seen.add(("skill", cap_id))
            skill = db.query(Skill).filter(Skill.id == cap_id).first()
            if skill:
                profiles.append(_profile_from_model(
                    skill,
                    cap_type="skill",
                    allowed=bool({"skill_read_md", "skill_run_script"} & allowed_set),
                    override=overrides.get(cap_id),
                ))
    except Exception:
        return []
    return profiles


def capability_profiles_to_classifier_hints(profiles: list[CapabilityProfile]) -> list[dict[str, str]]:
    return [
        profile.classifier_hint()
        for profile in profiles
        if profile.enabled and profile.allowed
    ]


def _fold_identifier(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _request_tokens(text: str) -> set[str]:
    tokens: set[str] = set()
    for token in [*_EN_TOKEN_RE.findall(text or ""), *_ZH_TOKEN_RE.findall(text or "")]:
        folded = token.strip().casefold()
        if folded and folded not in _STOPWORDS:
            tokens.add(folded)
    return tokens


def _term_matches(term: str, message: str, folded_message: str) -> bool:
    term = str(term or "").strip()
    if not term:
        return False
    folded = term.casefold()
    if re.search(r"[\u4e00-\u9fff]", term):
        return folded in folded_message
    if len(folded) <= 2:
        return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(folded)}(?![A-Za-z0-9_-])", folded_message) is not None
    return re.search(rf"(?<![A-Za-z0-9_-]){re.escape(folded)}(?![A-Za-z0-9_-])", folded_message) is not None


def _score_profile(profile: CapabilityProfile, message: str) -> tuple[int, list[str], bool]:
    folded = message.casefold()
    folded_compact = _fold_identifier(message)
    hits: list[str] = []
    score = 0
    explicit = False
    for term in _dedupe([profile.id, profile.name, *profile.aliases]):
        compact_term = _fold_identifier(term)
        compact_match = (
            len(compact_term) >= 3
            and re.fullmatch(r"[A-Za-z0-9._-]+", str(term or "").strip()) is not None
            and compact_term in folded_compact
        )
        if _term_matches(term, message, folded) or compact_match:
            explicit = True
            score += 100
            hits.append(term)
    for term in profile.keywords:
        if _term_matches(term, message, folded):
            score += 24
            hits.append(term)
    for term in profile.tags:
        if _term_matches(term, message, folded):
            score += 12
            hits.append(term)
    # Existing descriptions remain route metadata, but are deliberately weaker
    # than explicit hints to avoid polluting ordinary chat.
    request_tokens = _request_tokens(message)
    description_tokens = _request_tokens(profile.description)
    overlap = sorted(
        rt for rt in request_tokens
        if any(rt in dt or dt in rt for dt in description_tokens)
    )
    if overlap:
        score += min(18, 6 * len(overlap))
        hits.extend(overlap[:4])
    if score and _ACTION_INTENT_RE.search(message):
        score += 8
    return score, _dedupe(hits), explicit


def route_capabilities(
    user_message: str,
    profiles: list[CapabilityProfile],
    *,
    named_resources: list[str] | None = None,
) -> CapabilityRouteResult:
    """Return advisory capability candidates for the current message."""
    try:
        text = str(user_message or "").strip()
        if not text:
            return CapabilityRouteResult()
        named_resources = list(named_resources or [])
        is_action = bool(_ACTION_INTENT_RE.search(text))
        is_structured = bool(_STRUCTURED_INTENT_RE.search(text))
        is_knowledge = bool(_KNOWLEDGE_RE.search(text))
        scored: list[tuple[CapabilityCandidate, str]] = []
        blocked: list[dict[str, str]] = []
        matched_profile_keys: set[str] = set()
        for profile in profiles:
            score, hits, explicit = _score_profile(profile, text)
            if not score:
                continue
            matched_profile_keys.update(filter(None, [
                _fold_identifier(profile.id),
                _fold_identifier(profile.name),
                *[_fold_identifier(alias) for alias in profile.aliases],
            ]))
            qualifies = explicit or is_action or (is_structured and is_action) or (len(hits) >= 2 and not is_knowledge)
            if not qualifies:
                continue
            if not profile.enabled or not profile.allowed:
                blocked.append({
                    "type": profile.type,
                    "id": profile.id,
                    "reason": "disabled" if not profile.enabled else "permission_not_enabled",
                })
                continue
            reason = "explicit" if explicit else "metadata_match"
            scored.append((
                CapabilityCandidate(
                    type=profile.type,
                    id=profile.id,
                    name=profile.name,
                    score=score,
                    hit_terms=hits,
                    reason=reason,
                    description=profile.description,
                ),
                profile.type,
            ))
        missing = [
            name for name in named_resources
            if _fold_identifier(name) and _fold_identifier(name) not in matched_profile_keys
        ]
        if blocked:
            return CapabilityRouteResult(mode=ROUTE_BLOCKED_CAPABILITY, blocked=blocked, missing=missing)
        if missing:
            return CapabilityRouteResult(mode=ROUTE_MISSING_CAPABILITY, missing=_dedupe(missing))
        if not scored:
            return CapabilityRouteResult()
        scored.sort(key=lambda item: (-item[0].score, item[0].type, item[0].name.casefold(), item[0].id))
        mcp_candidates = [candidate for candidate, kind in scored if kind == "mcp"][:_MAX_MCP_CANDIDATES]
        skill_candidates = [candidate for candidate, kind in scored if kind == "skill"][:_MAX_SKILL_CANDIDATES]
        candidates = [*mcp_candidates, *skill_candidates]
        mode = ROUTE_EXPLICIT_CAPABILITY_REQUEST if any(item.reason == "explicit" for item in candidates) else ROUTE_CAPABILITY_CANDIDATE
        return CapabilityRouteResult(mode=mode, candidates=candidates)
    except Exception as exc:
        return CapabilityRouteResult(error=type(exc).__name__)


def build_candidate_capabilities_context(route: CapabilityRouteResult) -> str:
    if route.mode not in {ROUTE_CAPABILITY_CANDIDATE, ROUTE_EXPLICIT_CAPABILITY_REQUEST} or not route.candidates:
        return ""
    payload = {
        "route_mode": route.mode,
        "candidates": [candidate.to_context_dict() for candidate in route.candidates],
    }
    return (
        "【候选能力】以下 MCP / Skill 只是根据当前输入和当前 Agent 已绑定能力得到的建议；"
        "它们不会授予权限，也不会自动执行。若普通回答已足够，可以忽略；若需要外部能力，"
        "仍必须走现有 ReAct 规划、权限、preflight、MCP 惰性发现或 Skill 机制。\n"
        + json.dumps(payload, ensure_ascii=False)
    )


def redacted_route_step(trace: dict[str, Any]) -> dict[str, Any]:
    clean = json.loads(json.dumps(trace, ensure_ascii=False, default=str))
    text = json.dumps(clean, ensure_ascii=False)
    text = _CREDENTIAL_RE.sub(r"\1=[REDACTED]", text)
    try:
        clean = json.loads(text)
    except Exception:
        clean = {"route_mode": str(trace.get("route_mode") or ROUTE_NORMAL_CHAT)}
    return {
        "type": "capability_route",
        "action": "capability_route",
        "title": "能力路由",
        "status": "done",
        "detail": clean,
        "collapsed": True,
    }

"""Candidate construction for task-level model routing.

This module performs deterministic filtering only. Semantic model selection is
deliberately kept separate so no model name becomes a routing rule.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.deps import can_access_resource
from app.models import LLMResource, ModelRoleGroup, ModelRouteDecision, ModelRoutingPolicy, User
from app.security import new_id, now_str
from app.services.llm_client import LLMProviderThrottled, _raise_if_llm_throttle_circuit_open
from app.services.llm_client import chat_completion


_MEDIA_MODALITIES = frozenset({"image", "audio", "video"})
_MEDIA_EXTENSIONS = {
    "image": {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"},
    "audio": {".mp3", ".wav", ".m4a", ".ogg", ".opus", ".webm", ".flac"},
    "video": {".mp4", ".mov", ".mkv", ".avi", ".webm"},
}


def required_modalities_for_message(message_meta: dict | None) -> set[str]:
    """Infer required media modalities from attachment metadata, never task words.

    Inbound adapters provide lightweight attachment descriptors (``content_type``,
    ``media_type`` or filename). A text mentioning an image has no descriptor and
    therefore remains text-only.
    """
    meta = message_meta if isinstance(message_meta, dict) else {}
    attachments = meta.get("attachments") or meta.get("media") or []
    if isinstance(attachments, dict):
        attachments = [attachments]
    if not isinstance(attachments, list):
        attachments = []
    modalities: set[str] = set()
    for attachment in attachments:
        if not isinstance(attachment, dict):
            continue
        values = [
            str(attachment.get(key) or "").strip().lower()
            for key in ("modality", "media_type", "type", "content_type", "mime_type")
        ]
        for value in values:
            head = value.split("/", 1)[0]
            if value in _MEDIA_MODALITIES:
                modalities.add(value)
            elif head in _MEDIA_MODALITIES:
                modalities.add(head)
        filename = str(attachment.get("filename") or attachment.get("name") or "").lower()
        for modality, extensions in _MEDIA_EXTENSIONS.items():
            if any(filename.endswith(extension) for extension in extensions):
                modalities.add(modality)
    return modalities or {"text"}


@dataclass(frozen=True)
class ModelRouteCandidate:
    role: str
    group_id: str
    llm_id: str
    fallback_llm_ids: list[str]
    budget: int
    timeout: int
    capabilities: dict

    def to_prompt_dict(self) -> dict:
        return {
            "role": self.role,
            "group_id": self.group_id,
            "model_id": self.llm_id,
            "capabilities": self.capabilities,
        }


@dataclass(frozen=True)
class CandidateBuildResult:
    candidates: list[ModelRouteCandidate]
    exclusions: list[dict]


@dataclass(frozen=True)
class ModelRouteSelection:
    candidate: ModelRouteCandidate | None
    reason: str = ""
    failure: str = ""


def _capabilities(llm: LLMResource) -> dict:
    try:
        value = json.loads(llm.routing_capabilities or "{}")
    except json.JSONDecodeError:
        value = {}
    return value if isinstance(value, dict) else {}


def _exclude(exclusions: list[dict], llm_id: str, reason: str) -> None:
    exclusions.append({"model_id": llm_id, "reason": reason})


def build_route_candidates(
    db: Session,
    policy: ModelRoutingPolicy,
    user: User,
    *,
    runtime: str = "react",
    modalities: set[str] | None = None,
    required_context_tokens: int = 0,
) -> CandidateBuildResult:
    """Return policy candidates plus every deterministic exclusion reason."""
    required_modalities = set(modalities or {"text"})
    candidates: list[ModelRouteCandidate] = []
    exclusions: list[dict] = []
    try:
        group_ids = json.loads(policy.role_group_ids or "[]")
    except json.JSONDecodeError:
        group_ids = []
    for group_id in group_ids if isinstance(group_ids, list) else []:
        group = db.get(ModelRoleGroup, str(group_id))
        if group is None or not can_access_resource(user, group.visibility, group.allowed_users, group.creator):
            _exclude(exclusions, str(group_id), "group_unavailable")
            continue
        llm = db.get(LLMResource, group.preferred_llm_id)
        if llm is None:
            _exclude(exclusions, group.preferred_llm_id, "model_missing")
            continue
        if llm.type != "llm":
            _exclude(exclusions, llm.id, "not_leaf_model")
            continue
        if llm.id == policy.router_llm_id:
            _exclude(exclusions, llm.id, "router_self_reference")
            continue
        if not can_access_resource(user, llm.visibility, llm.allowed_users, llm.creator):
            _exclude(exclusions, llm.id, "model_unauthorized")
            continue
        capabilities = _capabilities(llm)
        if not capabilities.get("enabled", False):
            _exclude(exclusions, llm.id, "model_disabled")
            continue
        if group.role not in capabilities.get("roles", []):
            _exclude(exclusions, llm.id, "role_unsupported")
            continue
        if runtime not in capabilities.get("runtimes", []):
            _exclude(exclusions, llm.id, "runtime_unsupported")
            continue
        if not required_modalities.issubset(set(capabilities.get("modalities", []))):
            _exclude(exclusions, llm.id, "modality_unsupported")
            continue
        if int(llm.max_context_tokens or 0) < int(required_context_tokens or 0):
            _exclude(exclusions, llm.id, "context_insufficient")
            continue
        try:
            _raise_if_llm_throttle_circuit_open(llm)
        except LLMProviderThrottled:
            _exclude(exclusions, llm.id, "provider_throttled")
            continue
        fallbacks = json.loads(group.fallback_llm_ids or "[]")
        candidates.append(ModelRouteCandidate(
            role=group.role, group_id=group.id, llm_id=llm.id,
            fallback_llm_ids=[str(item) for item in fallbacks if str(item)],
            budget=group.budget, timeout=group.timeout, capabilities=capabilities,
        ))
    return CandidateBuildResult(candidates=candidates, exclusions=exclusions)


def build_ordered_fallback_candidates(
    db: Session,
    policy: ModelRoutingPolicy,
    user: User,
    selected: ModelRouteCandidate | None,
    *,
    runtime: str = "react",
    modalities: set[str] | None = None,
    required_context_tokens: int = 0,
) -> list[ModelRouteCandidate]:
    """Return the selected role group's explicitly configured eligible fallbacks.

    This is intentionally not a second policy search: order comes only from the
    selected group's ``fallback_llm_ids`` and every model receives the same
    compatibility and authorization gates as the initial candidate.
    """
    if selected is None:
        return []
    group = db.get(ModelRoleGroup, selected.group_id)
    if group is None or group.role != selected.role:
        return []
    required_modalities = set(modalities or {"text"})
    try:
        fallback_ids = json.loads(group.fallback_llm_ids or "[]")
    except json.JSONDecodeError:
        fallback_ids = []
    out: list[ModelRouteCandidate] = []
    for fallback_id in fallback_ids if isinstance(fallback_ids, list) else []:
        fallback_id = str(fallback_id or "").strip()
        if not fallback_id or fallback_id == selected.llm_id or fallback_id == policy.router_llm_id:
            continue
        llm = db.get(LLMResource, fallback_id)
        if llm is None or llm.type != "llm":
            continue
        if not can_access_resource(user, llm.visibility, llm.allowed_users, llm.creator):
            continue
        capabilities = _capabilities(llm)
        if (
            not capabilities.get("enabled", False)
            or group.role not in capabilities.get("roles", [])
            or runtime not in capabilities.get("runtimes", [])
            or not required_modalities.issubset(set(capabilities.get("modalities", [])))
            or int(llm.max_context_tokens or 0) < int(required_context_tokens or 0)
        ):
            continue
        try:
            _raise_if_llm_throttle_circuit_open(llm)
        except LLMProviderThrottled:
            continue
        out.append(ModelRouteCandidate(
            role=group.role,
            group_id=group.id,
            llm_id=llm.id,
            fallback_llm_ids=[],
            budget=group.budget,
            timeout=group.timeout,
            capabilities=capabilities,
        ))
    return out


def _route_prompt(user_message: str, candidates: list[ModelRouteCandidate]) -> list[dict]:
    return [
        {"role": "system", "content": (
            "你是模型路由器。只能从候选列表选择角色和 model_id。只输出 JSON："
            '{"role":"...","model_id":"...","reason":"..."}。'
        )},
        {"role": "user", "content": json.dumps({
            "task": (user_message or "")[:4000],
            "candidates": [candidate.to_prompt_dict() for candidate in candidates],
        }, ensure_ascii=False)},
    ]


def _default_candidate(policy: ModelRoutingPolicy, candidates: list[ModelRouteCandidate]) -> ModelRouteCandidate | None:
    return next((candidate for candidate in candidates if candidate.role == policy.default_role), None)


def parse_route_selection(response: str, policy: ModelRoutingPolicy, candidates: list[ModelRouteCandidate]) -> ModelRouteSelection:
    try:
        data = json.loads((response or "").strip())
    except (TypeError, ValueError, json.JSONDecodeError):
        return ModelRouteSelection(_default_candidate(policy, candidates), failure="invalid_json")
    if not isinstance(data, dict):
        return ModelRouteSelection(_default_candidate(policy, candidates), failure="invalid_shape")
    role = str(data.get("role") or "").strip()
    model_id = str(data.get("model_id") or "").strip()
    candidate = next((item for item in candidates if item.role == role and item.llm_id == model_id), None)
    if candidate is None:
        return ModelRouteSelection(_default_candidate(policy, candidates), failure="out_of_candidate_set")
    return ModelRouteSelection(candidate, reason=str(data.get("reason") or "").strip()[:500])


async def route_model(
    db: Session, policy: ModelRoutingPolicy, user_message: str, candidates: list[ModelRouteCandidate], *, timeout: int | None = None,
) -> ModelRouteSelection:
    router_llm = db.get(LLMResource, policy.router_llm_id)
    if router_llm is None or router_llm.type != "llm" or not candidates:
        return ModelRouteSelection(_default_candidate(policy, candidates), failure="router_or_candidates_unavailable")
    try:
        response = await chat_completion(router_llm, _route_prompt(user_message, candidates), max_tokens=256, db=db, timeout=timeout)
    except Exception:
        return ModelRouteSelection(_default_candidate(policy, candidates), failure="router_error")
    return parse_route_selection(response if isinstance(response, str) else "", policy, candidates)


def persist_route_decision(
    db: Session, *, agent_id: str, session_id: str, policy: ModelRoutingPolicy,
    selection: ModelRouteSelection, candidates: list[ModelRouteCandidate], exclusions: list[dict],
) -> ModelRouteDecision | None:
    if selection.candidate is None:
        return None
    decision = ModelRouteDecision(
        id=new_id(), agent_id=agent_id, session_id=session_id,
        policy_id=policy.id, policy_version=policy.version,
        role=selection.candidate.role, llm_id=selection.candidate.llm_id,
        detail=json.dumps({
            "reason": selection.reason, "failure": selection.failure,
            "candidate_ids": [candidate.llm_id for candidate in candidates],
            "exclusions": exclusions,
        }, ensure_ascii=False),
        created_at=now_str(),
    )
    db.add(decision)
    db.commit()
    return decision


def persist_fallback_route_decision(
    db: Session,
    *,
    agent_id: str,
    session_id: str,
    policy_id: str,
    policy_version: int,
    parent_decision_id: str,
    candidate: ModelRouteCandidate,
    failure_class: str,
) -> ModelRouteDecision:
    """Record the one allowed pre-output fallback without storing prompts."""
    decision = ModelRouteDecision(
        id=new_id(), agent_id=agent_id, session_id=session_id,
        policy_id=policy_id, policy_version=policy_version,
        role=candidate.role, llm_id=candidate.llm_id,
        detail=json.dumps({
            "kind": "pre_output_fallback",
            "parent_decision_id": parent_decision_id,
            "failure_class": failure_class,
            "candidate_id": candidate.llm_id,
        }, ensure_ascii=False),
        created_at=now_str(),
    )
    db.add(decision)
    db.commit()
    return decision


def frozen_llm_for_run(db: Session, agent_id: str, session_id: str) -> LLMResource | None:
    decision = (
        db.query(ModelRouteDecision)
        .filter(ModelRouteDecision.agent_id == agent_id, ModelRouteDecision.session_id == session_id)
        .order_by(ModelRouteDecision.created_at.desc(), ModelRouteDecision.id.desc())
        .first()
    )
    return db.get(LLMResource, decision.llm_id) if decision else None

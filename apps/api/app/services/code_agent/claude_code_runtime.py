"""Claude Code runtime preflight and adapter contracts.

This module intentionally keeps the first Claude Code integration point small:
preflight is executed through the existing run-bound runner, not by the API
process or host shell. Later adapter work can reuse the same result contract.
"""

from __future__ import annotations

import json
import hashlib
import re
import shlex
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

from app.services.code_agent.output_security import redact_code_output


@dataclass(frozen=True)
class ClaudeCodePreflightInput:
    run_id: str
    container_id: str
    repository_cwd: str = "/workspace"
    runtime_config_dir: str = "/workspace/.claude"
    mcp_config_json: str = "{}"
    model_config: Mapping[str, Any] = field(default_factory=dict)
    budget_watchdog_active: bool = True
    remaining_budget_seconds: int = 1
    timeout_seconds: int = 30


@dataclass(frozen=True)
class ClaudeCodePreflightStep:
    name: str
    status: str
    reason: str = ""
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "status": self.status,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class ClaudeCodePreflightResult:
    passed: bool
    failure_type: str = ""
    reason: str = ""
    claude_code_version: str = ""
    steps: tuple[ClaudeCodePreflightStep, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "failure_type": self.failure_type,
            "reason": self.reason,
            "claude_code_version": self.claude_code_version,
            "steps": [step.to_dict() for step in self.steps],
        }


ModelReachabilityProbe = Callable[[Mapping[str, Any]], bool]

CLAUDE_CODE_FAILURE_TYPES = frozenset({
    "runtime_unavailable",
    "model_unavailable",
    "mcp_config_failed",
    "skill_load_failed",
    "budget_exhausted",
    "coding_failed",
    "coding_timeout",
    "verification_failed",
    "infrastructure_error",
})

CLAUDE_CODE_NON_PATCH_TERMINAL_RESULTS = frozenset({
    "target_not_found",
    "needs_user_decision",
})

CLAUDE_CODE_EVENT_TYPES = frozenset({
    "runtime_started",
    "skill_loaded",
    "mcp_loaded",
    "tool_call",
    "file_changed",
    "test_run",
    "verifier_failed_retrying",
    "verifier_passed",
    "artifact_sealed",
})

_CLAUDE_REASON_TYPES = {
    "claude_code_cli_unavailable": "runtime_unavailable",
    "claude_code_config_dir_unwritable": "runtime_unavailable",
    "claude_code_repository_cwd_unwritable": "runtime_unavailable",
    "claude_code_model_unavailable": "model_unavailable",
    "llm_not_configured": "model_unavailable",
    "llm_not_found": "model_unavailable",
    "llm_group_not_supported": "model_unavailable",
    "llm_api_key_missing": "model_unavailable",
    "llm_model_missing": "model_unavailable",
    "claude_code_mcp_config_invalid": "mcp_config_failed",
    "claude_code_skill_load_failed": "skill_load_failed",
    "claude_code_budget_exhausted": "budget_exhausted",
    "claude_code_coding_timeout": "coding_timeout",
    "verification_failed": "verification_failed",
    "claude_code_budget_watchdog_unavailable": "infrastructure_error",
}


@dataclass(frozen=True)
class ClaudeCodeRuntimeInput:
    run_id: str
    container_id: str
    objective: str
    task_contract: Mapping[str, Any]
    effective_policy: Mapping[str, Any]
    model_config: Mapping[str, Any]
    repository_cwd: str = "/workspace"
    runtime_config_dir: str = "/workspace/.claude"
    allowed_tools: tuple[str, ...] = ()
    validation_plan: tuple[Mapping[str, Any], ...] = ()
    timeout_seconds: int = 1800
    max_turns: int = 40
    retry_attempt: int = 0
    verifier_feedback: str = ""
    session_name: str = ""
    exec_env: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "task_contract", MappingProxyType(dict(self.task_contract)))
        object.__setattr__(self, "effective_policy", MappingProxyType(dict(self.effective_policy)))
        object.__setattr__(self, "model_config", MappingProxyType(dict(self.model_config)))
        object.__setattr__(self, "allowed_tools", tuple(str(tool) for tool in self.allowed_tools))
        object.__setattr__(
            self,
            "validation_plan",
            tuple(MappingProxyType(dict(item)) for item in self.validation_plan),
        )
        object.__setattr__(self, "session_name", claude_code_session_name(self.run_id))
        object.__setattr__(
            self,
            "exec_env",
            MappingProxyType({str(key): str(value) for key, value in dict(self.exec_env).items()}),
        )


@dataclass(frozen=True)
class ClaudeCodeRuntimeResult:
    status: str
    exit_code: int
    summary: str = ""
    changed_files: tuple[str, ...] = ()
    budget_usage: Mapping[str, Any] = field(default_factory=dict)
    tool_audit: tuple[Mapping[str, Any], ...] = ()
    transcript_path: str = ""
    redacted_transcript_path: str = ""
    runtime_events: tuple[Mapping[str, Any], ...] = ()
    error_type: str = ""
    error_summary: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "changed_files", tuple(str(path) for path in self.changed_files))
        object.__setattr__(self, "budget_usage", MappingProxyType(dict(self.budget_usage)))
        object.__setattr__(
            self,
            "tool_audit",
            tuple(MappingProxyType(dict(item)) for item in self.tool_audit),
        )
        object.__setattr__(
            self,
            "runtime_events",
            tuple(MappingProxyType(dict(item)) for item in self.runtime_events),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "exit_code": self.exit_code,
            "summary": self.summary,
            "changed_files": list(self.changed_files),
            "budget_usage": dict(self.budget_usage),
            "tool_audit": [dict(item) for item in self.tool_audit],
            "transcript_path": self.transcript_path,
            "redacted_transcript_path": self.redacted_transcript_path,
            "runtime_events": [dict(item) for item in self.runtime_events],
            "error_type": self.error_type,
            "error_summary": self.error_summary,
        }


@dataclass(frozen=True)
class ClaudeCodeSkillFact:
    id: str
    name: str
    slug: str
    source: str
    version: str
    content_hash: str
    path: str

    def to_dict(self) -> dict[str, str]:
        return {
            "id": self.id,
            "name": self.name,
            "slug": self.slug,
            "source": self.source,
            "version": self.version,
            "content_hash": self.content_hash,
            "path": self.path,
        }


@dataclass(frozen=True)
class ClaudeCodeSkillInjectionResult:
    skills: tuple[ClaudeCodeSkillFact, ...]
    events: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "skills": [skill.to_dict() for skill in self.skills],
            "events": [dict(event) for event in self.events],
        }


@dataclass(frozen=True)
class ClaudeCodeMcpFact:
    id: str
    name: str
    authorization_state: str
    enabled_tool_count: int
    config_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "authorization_state": self.authorization_state,
            "enabled_tool_count": self.enabled_tool_count,
            "config_path": self.config_path,
        }


@dataclass(frozen=True)
class ClaudeCodeMcpInjectionResult:
    config_json: str
    config_path: str
    servers: tuple[ClaudeCodeMcpFact, ...]
    events: tuple[Mapping[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "config_path": self.config_path,
            "servers": [server.to_dict() for server in self.servers],
            "events": [dict(event) for event in self.events],
        }


def _claude_allowed_tools(allowed_tools: tuple[str, ...]) -> list[str]:
    mapped: set[str] = set()
    if {"read", "search"}.intersection(allowed_tools):
        mapped.update({"Read", "Grep"})
    if "edit" in allowed_tools:
        mapped.add("Edit")
    if {"test", "shell", "git_read"}.intersection(allowed_tools):
        mapped.add("Bash")
    return sorted(mapped)


def claude_code_session_name(run_id: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in str(run_id or ""))
    safe = safe.strip("-_") or "unknown"
    return f"code-agent-run-{safe}"


_SAFE_SKILL_SLUG = re.compile(r"[^A-Za-z0-9_.-]+")
_SAFE_ENV_NAME = re.compile(r"^[A-Z_][A-Z0-9_]*$")


SOP_ROOT = Path(__file__).resolve().parent / "sop"
SOP_SKILL_SLUGS = (
    "openspec-propose",
    "openspec-apply-change",
    "openspec-verify-change",
    "openspec-archive-change",
    "planning-with-files",
)
GRILL_CONFIRMATION = "开始实现"


def materialize_coding_sop(workspace_path: str) -> list[str]:
    """Copy baked SOP skills and CLAUDE.md into the run-local .claude directory."""
    root = Path(workspace_path).resolve()
    skills_root = root / ".claude" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for slug in SOP_SKILL_SLUGS:
        source = SOP_ROOT / "skills" / slug
        if not source.is_dir():
            continue
        target = skills_root / slug
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target)
        written.append(f".claude/skills/{slug}/SKILL.md")
    claude_src = SOP_ROOT / "CLAUDE.md"
    if claude_src.is_file():
        dest = root / ".claude" / "CLAUDE.md"
        dest.write_text(claude_src.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(".claude/CLAUDE.md")
    return written


def resolve_claude_code_exec_env(db, run) -> tuple[dict[str, str], str]:
    """Build Claude Code exec env from the Agent-bound LLM. Never persist the key."""
    from app.models import Agent

    agent = db.get(Agent, str(getattr(run, "agent_id", "") or ""))
    if agent is None or not str(getattr(agent, "llm_id", "") or "").strip():
        return {}, "llm_not_configured"
    reason = claude_code_llm_binding_reason(db, str(agent.llm_id))
    if reason:
        return {}, reason
    llm = claude_code_bound_llm(db, str(agent.llm_id))
    api_key = claude_code_llm_api_key(llm)
    env = {"ANTHROPIC_API_KEY": api_key}
    base_url = str(getattr(llm, "base_url", "") or "").strip()
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url
    return env, ""


def claude_code_bound_llm(db, llm_id: str):
    """Return the single LLMResource bound to Claude Code, or None.

    Claude Code runs must be reproducible. LLM groups are intentionally not
    resolved here because implicit member selection would make model/key choice
    non-deterministic for a frozen CodeAgent run.
    """
    from app.models import LLMResource

    ref = str(llm_id or "").strip()
    if not ref:
        return None
    return db.get(LLMResource, ref)


def claude_code_llm_api_key(llm) -> str:
    from app.security import decrypt_secret, is_masked_secret

    if llm is None:
        return ""
    try:
        api_key = decrypt_secret(getattr(llm, "api_key_enc", "") or "")
    except Exception:
        return ""
    return "" if is_masked_secret(api_key) else api_key


def claude_code_llm_binding_reason(db, llm_id: str) -> str:
    """Return empty string when the Agent-bound LLM can start Claude Code."""
    ref = str(llm_id or "").strip()
    if not ref:
        return "llm_not_configured"
    llm = claude_code_bound_llm(db, ref)
    if llm is None:
        return "llm_not_found"
    if str(getattr(llm, "type", "") or "llm").strip() == "group":
        return "llm_group_not_supported"
    if not claude_code_llm_api_key(llm):
        return "llm_api_key_missing"
    if not str(getattr(llm, "model", "") or "").strip():
        return "llm_model_missing"
    return ""


def _skill_slug(name: str, fallback: str) -> str:
    slug = _SAFE_SKILL_SLUG.sub("-", str(name or "").strip()).strip(".-")
    if not slug:
        slug = _SAFE_SKILL_SLUG.sub("-", str(fallback or "skill")).strip(".-")
    return slug or "skill"


def _allowed_skill_ids(run) -> list[str]:
    try:
        contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    values = contract.get("allowed_skills") if isinstance(contract, dict) else []
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if isinstance(value, str) and value.strip()]


def _allowed_mcp_ids(run) -> list[str]:
    try:
        contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    values = contract.get("authorized_mcp_servers") if isinstance(contract, dict) else []
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if isinstance(value, str) and value.strip()]


def _json_list(raw: str) -> list[str]:
    try:
        parsed = json.loads(raw or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed if isinstance(item, str)]


def _safe_env_references(raw: str) -> dict[str, str]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    safe: dict[str, str] = {}
    for key, value in parsed.items():
        env_key = str(key or "").strip()
        env_value = str(value or "").strip()
        if not _SAFE_ENV_NAME.fullmatch(env_key):
            continue
        if env_value.startswith("${") and env_value.endswith("}"):
            ref_name = env_value[2:-1]
        elif env_value.startswith("$"):
            ref_name = env_value[1:]
        else:
            continue
        if _SAFE_ENV_NAME.fullmatch(ref_name):
            safe[env_key] = f"${{{ref_name}}}"
    return safe


def materialize_claude_code_mcp_config(db, run, workspace_path: str) -> ClaudeCodeMcpInjectionResult:
    """Write run-local Claude Code MCP config for frozen authorized MCP servers."""
    from pathlib import Path

    from app.models import MCP

    root = Path(workspace_path).resolve()
    config_dir = root / ".claude"
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / "mcp.json"
    rel_config_path = config_path.relative_to(root).as_posix()
    mcp_servers: dict[str, dict[str, Any]] = {}
    facts: list[ClaudeCodeMcpFact] = []
    events: list[Mapping[str, Any]] = []
    for mcp_id in _allowed_mcp_ids(run):
        mcp = db.query(MCP).filter(MCP.id == mcp_id).first()
        if not mcp:
            continue
        name = _skill_slug(mcp.name, mcp.id)
        args = _json_list(getattr(mcp, "command_args", "") or "[]")
        server_config: dict[str, Any] = {}
        if getattr(mcp, "command", ""):
            server_config["command"] = mcp.command
            if args:
                server_config["args"] = args
            env = _safe_env_references(getattr(mcp, "command_env", "") or "{}")
            if env:
                server_config["env"] = env
        elif getattr(mcp, "url", ""):
            server_config["type"] = str(getattr(mcp, "protocol", "") or "sse")
            server_config["url"] = mcp.url
        else:
            continue
        mcp_servers[name] = server_config
        fact = ClaudeCodeMcpFact(
            id=mcp.id,
            name=mcp.name or mcp.id,
            authorization_state="authorized",
            enabled_tool_count=0,
            config_path=rel_config_path,
        )
        facts.append(fact)
        events.append({
            "type": "mcp_loaded",
            "run_id": str(getattr(run, "id", "") or ""),
            "mcp_id": fact.id,
            "mcp_name": fact.name,
            "authorization_state": fact.authorization_state,
            "enabled_tool_count": fact.enabled_tool_count,
        })
    config = {"mcpServers": mcp_servers}
    config_json = json.dumps(config, sort_keys=True)
    config_path.write_text(config_json, encoding="utf-8")
    return ClaudeCodeMcpInjectionResult(
        config_json=config_json,
        config_path=rel_config_path,
        servers=tuple(facts),
        events=tuple(events),
    )


def record_claude_code_mcp_injection(run, result: ClaudeCodeMcpInjectionResult) -> None:
    try:
        runner_facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        runner_facts = {}
    if not isinstance(runner_facts, dict):
        runner_facts = {}
    runner_facts["claude_code_mcp"] = result.to_dict()
    run.runner_facts = json.dumps(runner_facts, sort_keys=True)


def materialize_claude_code_skills(db, run, workspace_path: str) -> ClaudeCodeSkillInjectionResult:
    """Write authorized Skill markdown into the run-local Claude Code skill dir."""
    from pathlib import Path

    from app.models import Skill
    from app.services import skill_runtime

    root = Path(workspace_path).resolve()
    skills_root = root / ".claude" / "skills"
    skills_root.mkdir(parents=True, exist_ok=True)
    facts: list[ClaudeCodeSkillFact] = []
    events: list[Mapping[str, Any]] = []
    for skill_id in _allowed_skill_ids(run):
        skill = db.query(Skill).filter(Skill.id == skill_id).first()
        if not skill:
            continue
        markdown = skill_runtime.read_md(skill) or ""
        skill_source_dir = skill_runtime._skill_dir(skill)
        digest = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
        slug = _skill_slug(skill.name, skill.id)
        skill_dir = skills_root / slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(markdown, encoding="utf-8")
        resource_count = 0
        if skill_source_dir:
            source_root = Path(skill_source_dir).resolve()
            for source_file in sorted(source_root.rglob("*")):
                if not source_file.is_file() or source_file.is_symlink():
                    continue
                try:
                    relative = source_file.resolve().relative_to(source_root)
                except ValueError:
                    continue
                if relative.as_posix() == "SKILL.md":
                    continue
                target = (skill_dir / relative).resolve()
                try:
                    target.relative_to(skill_dir.resolve())
                except ValueError:
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(source_file.read_bytes())
                resource_count += 1
        rel_path = skill_file.relative_to(root).as_posix()
        source = f"zip:{skill.zip_name}" if getattr(skill, "zip_name", "") else f"skill:{skill.id}"
        version = str(getattr(skill, "modified_at", "") or getattr(skill, "zip_name", "") or "")
        fact = ClaudeCodeSkillFact(
            id=skill.id,
            name=skill.name or skill.id,
            slug=slug,
            source=source,
            version=version,
            content_hash=digest,
            path=rel_path,
        )
        facts.append(fact)
        events.append({
            "type": "skill_loaded",
            "run_id": str(getattr(run, "id", "") or ""),
            "skill_id": fact.id,
            "skill_name": fact.name,
            "source": fact.source,
            "version": fact.version,
            "content_hash": fact.content_hash,
            "resource_count": resource_count,
        })
    return ClaudeCodeSkillInjectionResult(skills=tuple(facts), events=tuple(events))


def record_claude_code_skill_injection(run, result: ClaudeCodeSkillInjectionResult) -> None:
    try:
        runner_facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        runner_facts = {}
    if not isinstance(runner_facts, dict):
        runner_facts = {}
    runner_facts["claude_code_skills"] = result.to_dict()
    run.runner_facts = json.dumps(runner_facts, sort_keys=True)


def _runtime_summary(exit_code: int, output: str) -> str:
    raw = str(output or "").strip()
    if not raw:
        return ""
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw[:1000]
    if isinstance(parsed, dict):
        for key in ("summary", "result", "text", "message"):
            value = parsed.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:1000]
    return raw[:1000]


def classify_claude_code_failure(reason: str, *, exit_code: int = 1) -> str:
    raw = str(reason or "").strip()
    if raw in CLAUDE_CODE_FAILURE_TYPES:
        return raw
    if raw in _CLAUDE_REASON_TYPES:
        return _CLAUDE_REASON_TYPES[raw]
    lowered = raw.lower()
    if exit_code == 124 or "timeout" in lowered or "timed out" in lowered:
        return "coding_timeout"
    return "coding_failed"


def _runtime_payload(output: str) -> dict[str, Any]:
    try:
        parsed = json.loads(str(output or "").strip() or "{}")
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_code_output(value).text
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    if isinstance(value, dict):
        return {str(key): _redact_value(item) for key, item in value.items()}
    return value


def normalize_claude_code_runtime_event(event: Mapping[str, Any], *, run_id: str) -> dict[str, Any]:
    raw_type = str(event.get("type") or "").strip()
    event_type = raw_type if raw_type in CLAUDE_CODE_EVENT_TYPES else "tool_call"
    payload: dict[str, Any] = {
        "version": 1,
        "profile": "code",
        "phase": event_type,
        "status": str(event.get("status") or "completed"),
        "run_id": str(run_id or event.get("run_id") or ""),
    }
    for key in (
        "runtime",
        "skill_id",
        "skill_name",
        "mcp_id",
        "mcp_name",
        "authorization_state",
        "enabled_tool_count",
        "tool",
        "path",
        "command",
        "summary",
        "artifact_id",
        "retry_attempt",
        "max_retries",
    ):
        if key in event:
            payload[key] = _redact_value(event[key])
    return payload


def _string_tuple(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list):
        return ()
    return tuple(str(item) for item in raw if isinstance(item, str) and item.strip())


def _dict_tuple(raw: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(raw, list):
        return ()
    rows = []
    for item in raw:
        if isinstance(item, dict):
            rows.append(_redact_value(item))
    return tuple(rows)


def write_claude_code_transcripts(
    workspace_path: str,
    *,
    run_id: str,
    transcript: str,
) -> tuple[str, str]:
    from pathlib import Path

    root = Path(workspace_path).resolve()
    transcript_dir = root / ".claude" / "transcripts"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    safe_run_id = claude_code_session_name(run_id).replace("code-agent-run-", "", 1)
    raw_path = transcript_dir / f"{safe_run_id}.jsonl"
    redacted_path = transcript_dir / f"{safe_run_id}.redacted.jsonl"
    raw_path.write_text(str(transcript or ""), encoding="utf-8")
    redacted_path.write_text(redact_code_output(str(transcript or "")).text, encoding="utf-8")
    return raw_path.relative_to(root).as_posix(), redacted_path.relative_to(root).as_posix()


def _build_claude_code_command(runtime_input: ClaudeCodeRuntimeInput) -> str:
    model_ref = str(runtime_input.model_config.get("model_ref") or "").strip()
    if not model_ref:
        model_ref = "sonnet"
    prompt = runtime_input.objective
    if runtime_input.retry_attempt > 0 and runtime_input.verifier_feedback.strip():
        prompt = (
            f"{prompt}\n\nVerifier feedback for retry {runtime_input.retry_attempt}:\n"
            f"{redact_code_output(runtime_input.verifier_feedback).text}"
        )
    prompt = (
        "Follow the coding SOP in /workspace/.claude/CLAUDE.md. "
        f"{prompt}"
    )
    parts = [
        "cd",
        shlex.quote(runtime_input.repository_cwd),
        "&&",
        "claude",
        "-p",
        shlex.quote(prompt),
        "--output-format",
        "json",
        "--model",
        shlex.quote(model_ref),
        "--max-turns",
        str(max(1, int(runtime_input.max_turns))),
        "--permission-mode",
        "acceptEdits",
        "--no-chrome",
    ]
    if runtime_input.retry_attempt > 0:
        parts.extend(["--resume", shlex.quote(runtime_input.session_name)])
    else:
        parts.extend(["--name", shlex.quote(runtime_input.session_name)])
    allowed = _claude_allowed_tools(runtime_input.allowed_tools)
    if allowed:
        parts.extend(["--allowedTools", *[shlex.quote(tool) for tool in allowed]])
    return " ".join(parts)


class ClaudeCodeRuntimeAdapter:
    """Run Claude Code as the CodeAgent coding backend through the existing runner."""

    def __init__(self, *, runner):
        self.runner = runner

    def run(self, runtime_input: ClaudeCodeRuntimeInput) -> ClaudeCodeRuntimeResult:
        command = _build_claude_code_command(runtime_input)
        exit_code, output = _runner_exec(
            self.runner,
            runtime_input.container_id,
            command,
            max(1, int(runtime_input.timeout_seconds)),
            environment=dict(runtime_input.exec_env),
        )
        payload = _runtime_payload(output)
        summary = redact_code_output(_runtime_summary(exit_code, output)).text
        requested_status = str(payload.get("status") or "").strip()
        error_type = "" if exit_code == 0 else classify_claude_code_failure(summary, exit_code=exit_code)
        if exit_code == 0 and requested_status in CLAUDE_CODE_NON_PATCH_TERMINAL_RESULTS:
            status = requested_status
        else:
            status = "coding_completed" if exit_code == 0 else error_type
        budget_usage = _redact_value(payload.get("budget_usage", {}))
        if not isinstance(budget_usage, dict):
            budget_usage = {}
        return ClaudeCodeRuntimeResult(
            status=status,
            exit_code=exit_code,
            summary=summary,
            changed_files=_string_tuple(payload.get("changed_files")),
            budget_usage=budget_usage,
            tool_audit=_dict_tuple(payload.get("tool_audit")),
            transcript_path=redact_code_output(str(payload.get("transcript_path") or "")).text,
            redacted_transcript_path=redact_code_output(
                str(payload.get("redacted_transcript_path") or "")
            ).text,
            runtime_events=(
                *_dict_tuple(payload.get("runtime_events")),
                {
                    "type": "runtime_started",
                    "run_id": runtime_input.run_id,
                    "runtime": "claude_code",
                    "repository_cwd": runtime_input.repository_cwd,
                },
            ),
            error_type=error_type,
            error_summary="" if exit_code == 0 else summary,
        )


def record_claude_code_runtime_result(run, result: ClaudeCodeRuntimeResult) -> None:
    try:
        runner_facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        runner_facts = {}
    if not isinstance(runner_facts, dict):
        runner_facts = {}
    history = runner_facts.get("claude_code_runtime_history")
    if not isinstance(history, list):
        history = []
    history.append(result.to_dict())
    runner_facts["claude_code_runtime_history"] = history[-10:]
    runner_facts["claude_code_runtime"] = result.to_dict()
    run.runner_facts = json.dumps(runner_facts, sort_keys=True)

    try:
        budget_usage = json.loads(getattr(run, "budget_usage", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        budget_usage = {}
    if not isinstance(budget_usage, dict):
        budget_usage = {}
    budget_usage["claude_code_runtime"] = {
        **dict(result.budget_usage),
        "exit_code": result.exit_code,
        "changed_files": len(result.changed_files),
    }
    run.budget_usage = json.dumps(_redact_value(budget_usage), sort_keys=True)

    try:
        audit = json.loads(getattr(run, "tool_audit", "") or "[]")
    except (TypeError, json.JSONDecodeError):
        audit = []
    if not isinstance(audit, list):
        audit = []
    audit.extend(dict(item) for item in result.tool_audit)
    run.tool_audit = json.dumps(_redact_value(audit[-1000:]), sort_keys=True)


def claude_code_max_verifier_retries(run) -> int:
    try:
        contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    try:
        policy = json.loads(getattr(run, "effective_policy", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        policy = {}
    runtime_budgets = {}
    if isinstance(contract, dict) and isinstance(contract.get("runtime_budgets"), dict):
        runtime_budgets = contract["runtime_budgets"]
    elif isinstance(policy, dict) and isinstance(policy.get("runtime_budgets"), dict):
        runtime_budgets = policy["runtime_budgets"]
    try:
        value = int((runtime_budgets or {}).get("max_verifier_retries", 2))
    except (TypeError, ValueError):
        value = 2
    return max(0, min(value, 2))


def claude_code_verifier_feedback(report) -> str:
    if hasattr(report, "to_dict"):
        payload = report.to_dict()
    else:
        payload = {
            "passed": bool(getattr(report, "passed", False)),
            "outcome": str(getattr(report, "outcome", "")),
            "reason": str(getattr(report, "reason", "")),
            "changed_paths": list(getattr(report, "changed_paths", ()) or ()),
            "checks": list(getattr(report, "checks", ()) or ()),
            "tests": list(getattr(report, "tests", ()) or ()),
        }
    safe_payload = {
        "outcome": payload.get("outcome"),
        "reason": payload.get("reason"),
        "changed_paths": payload.get("changed_paths") or [],
        "checks": payload.get("checks") or [],
        "tests": [
            {
                "test_index": item.get("test_index"),
                "command": item.get("command"),
                "exit_code": item.get("exit_code"),
                "failure_classification": item.get("failure_classification"),
                "output": str(item.get("output") or "")[:4000],
            }
            for item in (payload.get("tests") or [])
            if isinstance(item, dict)
        ],
        "secret_scan_complete": bool(payload.get("secret_scan_complete")),
    }
    return redact_code_output(json.dumps(safe_payload, sort_keys=True, ensure_ascii=False)).text


def runtime_input_from_execution(
    execution,
    run,
    *,
    retry_attempt: int = 0,
    verifier_feedback: str = "",
    exec_env: Mapping[str, str] | None = None,
) -> ClaudeCodeRuntimeInput:
    try:
        contract = json.loads(getattr(execution, "task_contract_json", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    try:
        policy = json.loads(getattr(execution, "effective_policy_json", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        policy = {}
    model_config = {}
    if isinstance(contract, dict) and isinstance(contract.get("model_config"), dict):
        model_config = contract["model_config"]
    elif isinstance(policy, dict) and isinstance(policy.get("model_config"), dict):
        model_config = policy["model_config"]
    budgets = policy.get("budgets") if isinstance(policy, dict) else {}
    allowed_tools = policy.get("allowed_tools") if isinstance(policy, dict) else []
    validation_plan = contract.get("validation_plan") if isinstance(contract, dict) else []
    return ClaudeCodeRuntimeInput(
        run_id=str(getattr(run, "id", "") or getattr(execution, "run_id", "") or ""),
        container_id=str(getattr(execution, "container_id", "") or getattr(run, "container_id", "") or ""),
        objective=str(contract.get("objective") or ""),
        task_contract=contract if isinstance(contract, dict) else {},
        effective_policy=policy if isinstance(policy, dict) else {},
        model_config=model_config,
        repository_cwd="/workspace",
        allowed_tools=tuple(str(tool) for tool in (allowed_tools or []) if isinstance(tool, str)),
        validation_plan=tuple(item for item in (validation_plan or []) if isinstance(item, dict)),
        timeout_seconds=int(getattr(execution, "timeout_seconds", 1800) or 1800),
        max_turns=int((budgets or {}).get("max_iterations") or 40),
        retry_attempt=max(0, int(retry_attempt or 0)),
        verifier_feedback=verifier_feedback,
        exec_env=exec_env or {},
    )


def _step(name: str, status: str, reason: str = "", detail: str = "") -> ClaudeCodePreflightStep:
    return ClaudeCodePreflightStep(
        name=name,
        status=status,
        reason=reason,
        detail=str(detail or "")[:256],
    )


def _failed(
    steps: list[ClaudeCodePreflightStep],
    *,
    name: str,
    failure_type: str,
    reason: str,
    detail: str = "",
) -> ClaudeCodePreflightResult:
    steps.append(_step(name, "failed", reason, detail))
    return ClaudeCodePreflightResult(
        passed=False,
        failure_type=failure_type,
        reason=reason,
        steps=tuple(steps),
    )


def _runner_exec(
    runner,
    container_id: str,
    command: str,
    timeout_seconds: int,
    environment: Mapping[str, str] | None = None,
) -> tuple[int, str]:
    try:
        exit_code, output = runner.exec(
            container_id,
            command,
            timeout_seconds=timeout_seconds,
            environment=dict(environment or {}),
        )
    except Exception as exc:
        return 1, f"{type(exc).__name__}: {exc}"
    return int(exit_code), str(output or "")


def archive_openspec_after_seal(runner, run) -> dict[str, Any]:
    """Archive OpenSpec changes in the workspace after a successful seal."""
    container_id = str(getattr(run, "container_id", "") or "")
    archived: list[str] = []
    if not container_id:
        return {"archived": archived, "reason": "runner_unavailable"}
    start = getattr(runner, "start_stopped", None)
    if callable(start):
        try:
            start(container_id)
        except Exception:
            return {"archived": archived, "reason": "runner_unavailable"}
    exit_code, output = _runner_exec(
        runner, container_id, "openspec list --json", 60,
    )
    if exit_code != 0:
        return {"archived": archived, "reason": "openspec_list_failed"}
    try:
        payload = json.loads(output or "{}")
    except json.JSONDecodeError:
        return {"archived": archived, "reason": "openspec_list_invalid"}
    changes = payload.get("changes") if isinstance(payload, dict) else []
    if not isinstance(changes, list):
        return {"archived": archived, "reason": "openspec_list_invalid"}
    for item in changes:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        status = str(item.get("status") or "").strip()
        if not name or status == "complete":
            continue
        code, _out = _runner_exec(
            runner,
            container_id,
            f"openspec archive {shlex.quote(name)} -y --json",
            120,
        )
        if code == 0:
            archived.append(name)
    freeze = getattr(runner, "freeze", None)
    if callable(freeze):
        try:
            freeze(container_id)
        except Exception:
            pass
    return {"archived": archived, "reason": ""}


def record_claude_code_openspec_archive(run, result: Mapping[str, Any]) -> None:
    """Persist sanitized OpenSpec archive evidence on the run."""
    try:
        runner_facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        runner_facts = {}
    if not isinstance(runner_facts, dict):
        runner_facts = {}
    runner_facts["claude_code_openspec_archive"] = _redact_value({
        "archived": result.get("archived", []) if isinstance(result, Mapping) else [],
        "reason": result.get("reason", "") if isinstance(result, Mapping) else "",
    })
    run.runner_facts = json.dumps(runner_facts, sort_keys=True)


def _parse_mcp_config(raw: str) -> tuple[bool, str]:
    try:
        parsed = json.loads(raw or "{}")
    except (TypeError, json.JSONDecodeError) as exc:
        return False, str(exc)
    if not isinstance(parsed, dict):
        return False, "mcp_config_not_object"
    servers = parsed.get("mcpServers", {})
    if servers is not None and not isinstance(servers, dict):
        return False, "mcp_servers_not_object"
    return True, ""


def _default_model_probe(model_config: Mapping[str, Any]) -> bool:
    if str(model_config.get("model_binding_reason") or "").strip():
        return False
    provider = str(model_config.get("provider") or "").strip()
    model_ref = str(model_config.get("model_ref") or "").strip()
    return provider == "cloud_claude" and bool(model_ref)


def code_run_uses_claude_code(run) -> bool:
    try:
        contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    if isinstance(contract, dict) and contract.get("coding_runtime") == "claude_code":
        return True
    try:
        policy = json.loads(getattr(run, "effective_policy", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        policy = {}
    return isinstance(policy, dict) and policy.get("coding_runtime") == "claude_code"


def preflight_input_from_run(
    run,
    runner_facts,
    *,
    mcp_config_json: str = "{}",
) -> ClaudeCodePreflightInput:
    try:
        contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        contract = {}
    try:
        policy = json.loads(getattr(run, "effective_policy", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        policy = {}
    model_config = {}
    if isinstance(contract, dict) and isinstance(contract.get("model_config"), dict):
        model_config = contract["model_config"]
    elif isinstance(policy, dict) and isinstance(policy.get("model_config"), dict):
        model_config = policy["model_config"]
    budgets = policy.get("budgets") if isinstance(policy, dict) else {}
    timeout_seconds = int((budgets or {}).get("timeout_seconds") or 30)
    workspace_mount = str(getattr(runner_facts, "workspace_mount", "") or "/workspace")
    return ClaudeCodePreflightInput(
        run_id=str(getattr(run, "id", "") or ""),
        container_id=str(getattr(runner_facts, "container_id", "") or getattr(run, "container_id", "") or ""),
        repository_cwd=workspace_mount,
        runtime_config_dir=f"{workspace_mount.rstrip('/')}/.claude",
        mcp_config_json=mcp_config_json,
        model_config=model_config,
        budget_watchdog_active=timeout_seconds > 0,
        remaining_budget_seconds=timeout_seconds,
        timeout_seconds=min(max(1, timeout_seconds), 30),
    )


def record_claude_code_preflight(run, result: ClaudeCodePreflightResult) -> None:
    try:
        facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except (TypeError, json.JSONDecodeError):
        facts = {}
    if not isinstance(facts, dict):
        facts = {}
    facts["claude_code_preflight"] = result.to_dict()
    run.runner_facts = json.dumps(facts, sort_keys=True)


def run_claude_code_preflight(
    preflight_input: ClaudeCodePreflightInput,
    *,
    runner,
    model_probe: ModelReachabilityProbe | None = None,
) -> ClaudeCodePreflightResult:
    """Run Claude Code runtime preflight inside the existing runner container."""
    steps: list[ClaudeCodePreflightStep] = []
    timeout = max(1, int(preflight_input.timeout_seconds))
    container_id = str(preflight_input.container_id or "").strip()
    repository_cwd = str(preflight_input.repository_cwd or "").strip()
    runtime_config_dir = str(preflight_input.runtime_config_dir or "").strip()
    if not container_id:
        return _failed(
            steps,
            name="runner_binding",
            failure_type="infrastructure_error",
            reason="claude_code_container_missing",
        )

    exit_code, output = _runner_exec(runner, container_id, "claude --version", timeout)
    claude_code_version = output.strip().splitlines()[0] if output.strip() else ""
    if exit_code != 0 or not claude_code_version:
        return _failed(
            steps,
            name="cli",
            failure_type="runtime_unavailable",
            reason="claude_code_cli_unavailable",
            detail=output,
        )
    steps.append(_step("cli", "passed", detail=claude_code_version))

    config_dir = shlex.quote(runtime_config_dir)
    exit_code, output = _runner_exec(
        runner,
        container_id,
        f"mkdir -p {config_dir} && test -d {config_dir} && test -w {config_dir}",
        timeout,
    )
    if exit_code != 0:
        return _failed(
            steps,
            name="config_dir",
            failure_type="runtime_unavailable",
            reason="claude_code_config_dir_unwritable",
            detail=output,
        )
    steps.append(_step("config_dir", "passed"))

    cwd = shlex.quote(repository_cwd)
    probe_path = shlex.quote(f"{repository_cwd.rstrip('/')}/.code-agent-preflight-write")
    exit_code, output = _runner_exec(
        runner,
        container_id,
        f"test -d {cwd} && test -r {cwd} && test -w {cwd} && touch {probe_path} && rm -f {probe_path}",
        timeout,
    )
    if exit_code != 0:
        return _failed(
            steps,
            name="repository_cwd",
            failure_type="runtime_unavailable",
            reason="claude_code_repository_cwd_unwritable",
            detail=output,
        )
    steps.append(_step("repository_cwd", "passed"))

    mcp_ok, mcp_detail = _parse_mcp_config(preflight_input.mcp_config_json)
    if not mcp_ok:
        return _failed(
            steps,
            name="mcp_config",
            failure_type="mcp_config_failed",
            reason="claude_code_mcp_config_invalid",
            detail=mcp_detail,
        )
    steps.append(_step("mcp_config", "passed"))

    probe = model_probe or _default_model_probe
    try:
        model_ok = bool(probe(preflight_input.model_config))
    except Exception as exc:
        model_ok = False
        model_detail = f"{type(exc).__name__}: {exc}"
    else:
        model_detail = ""
    if not model_ok:
        reason = str(
            preflight_input.model_config.get("model_binding_reason") or ""
        ).strip() or "claude_code_model_unavailable"
        return _failed(
            steps,
            name="model",
            failure_type="model_unavailable",
            reason=reason,
            detail=model_detail,
        )
    steps.append(_step("model", "passed"))

    if not preflight_input.budget_watchdog_active:
        return _failed(
            steps,
            name="budget_watchdog",
            failure_type="infrastructure_error",
            reason="claude_code_budget_watchdog_unavailable",
        )
    if int(preflight_input.remaining_budget_seconds) <= 0:
        return _failed(
            steps,
            name="budget",
            failure_type="budget_exhausted",
            reason="claude_code_budget_exhausted",
        )
    steps.append(_step("budget_watchdog", "passed"))

    return ClaudeCodePreflightResult(
        passed=True,
        claude_code_version=claude_code_version,
        steps=tuple(steps),
    )

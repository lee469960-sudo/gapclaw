"""Stable CodeAgent terminal result presentation shared by API and UI."""

from __future__ import annotations

import json

from app.services.code_agent.failures import external_failure
from app.services.code_agent.claude_code_runtime import claude_code_llm_repair_guidance
from app.services.code_agent.output_security import redact_code_output


TERMINAL_PRESENTATION = {
    "patch_ready": ("Patch 已验证", "success"),
    "stale": ("基线已过期", "warning"),
    "no_change_justified": ("无需代码变更", "success"),
    "verification_inconclusive": ("验证不充分", "warning"),
    "baseline_broken": ("基线测试已失败", "warning"),
    "verification_failed": ("验证失败", "danger"),
    "budget_exhausted": ("预算已耗尽", "warning"),
    "no_progress": ("未取得有效进展", "warning"),
    "policy_rejected": ("策略拒绝", "danger"),
    "runtime_unavailable": ("Claude Code Runtime 不可用", "danger"),
    "model_unavailable": ("Claude Code 模型不可用", "danger"),
    "mcp_config_failed": ("Claude Code MCP 配置失败", "danger"),
    "skill_load_failed": ("Claude Code Skill 加载失败", "danger"),
    "coding_failed": ("Claude Code 编码失败", "danger"),
    "coding_timeout": ("Claude Code 编码超时", "warning"),
    "target_not_found": ("目标未找到", "warning"),
    "needs_user_decision": ("需要用户决策", "warning"),
    "workspace_integrity_error": ("Workspace 完整性失败", "danger"),
    "infrastructure_error": ("基础设施失败", "danger"),
    "cancelled": ("已取消", "info"),
    "timed_out": ("已超时", "warning"),
    "failed": ("执行失败", "danger"),
}


def _redact_runtime_value(value):
    if isinstance(value, dict):
        return {str(key): _redact_runtime_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact_runtime_value(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_runtime_value(item) for item in value]
    if isinstance(value, str):
        text = redact_code_output(value).text
        return text.encode("utf-8", errors="replace").decode("utf-8")
    return value


def _runtime_code_snippets(runtime: dict) -> list[dict[str, str]]:
    """Collect bounded, redacted code contexts from Claude runtime events."""
    runtime_result = runtime.get("runtime_result")
    if not isinstance(runtime_result, dict):
        return []
    events = runtime_result.get("runtime_events")
    if not isinstance(events, list):
        return []
    snippets: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        phase = str(event.get("type") or event.get("phase") or "").strip()
        if phase not in {"file_changed", "tool_call", "test_run"}:
            continue
        snippet = event.get("snippet") or event.get("output") or ""
        if not isinstance(snippet, str) or not snippet.strip():
            continue
        path = str(event.get("path") or "").strip()
        command = str(event.get("command") or "").strip()
        text = redact_code_output(snippet).text
        text = text.encode("utf-8", errors="replace").decode("utf-8")
        text = text[:3000] + ("\n…（片段已截断）" if len(text) > 3000 else "")
        key = (phase, path, text)
        if key in seen:
            continue
        seen.add(key)
        snippets.append({
            "phase": phase,
            "path": path,
            "command": command,
            "snippet": text,
        })
        if len(snippets) >= 12:
            break
    return snippets


def serialize_code_result(run, artifact=None) -> dict:
    try:
        report = json.loads(run.verifier_report or "{}")
    except json.JSONDecodeError:
        report = {}
    try:
        budget_usage = json.loads(getattr(run, "budget_usage", "") or "{}")
    except json.JSONDecodeError:
        budget_usage = {}
    try:
        runner_facts = json.loads(getattr(run, "runner_facts", "") or "{}")
    except json.JSONDecodeError:
        runner_facts = {}
    try:
        task_contract = json.loads(getattr(run, "task_contract", "") or "{}")
    except json.JSONDecodeError:
        task_contract = {}
    try:
        effective_policy = json.loads(getattr(run, "effective_policy", "") or "{}")
    except json.JSONDecodeError:
        effective_policy = {}
    if not isinstance(runner_facts, dict):
        runner_facts = {}
    if not isinstance(task_contract, dict):
        task_contract = {}
    if not isinstance(effective_policy, dict):
        effective_policy = {}
    try:
        source_facts = json.loads(getattr(run, "source_facts", "") or "{}")
    except json.JSONDecodeError:
        source_facts = {}
    if not isinstance(source_facts, dict):
        source_facts = {}
    coding_runtime = (
        task_contract.get("coding_runtime")
        or effective_policy.get("coding_runtime")
        or "legacy"
    )
    preflight = runner_facts.get("claude_code_preflight") or {}
    model_reason = preflight.get("reason") or getattr(run, "failure_reason", "")
    model_guidance = claude_code_llm_repair_guidance(model_reason)
    runtime_summary = _redact_runtime_value({
        "coding_runtime": str(coding_runtime),
        "preflight": runner_facts.get("claude_code_preflight") or {},
        "skills": (runner_facts.get("claude_code_skills") or {}).get("skills", []),
        "mcp_servers": (runner_facts.get("claude_code_mcp") or {}).get("servers", []),
        "runtime_result": runner_facts.get("claude_code_runtime") or {},
        "readiness": {
            "workspace_files": {"status": "passed" if getattr(run, "workspace_path", "") or source_facts.get("path") else "failed", "reason": "" if getattr(run, "workspace_path", "") or source_facts.get("path") else "workspace_not_materialized"},
            "git_metadata": {"status": "passed" if source_facts.get("repo_root_ready") is True else "failed", "reason": source_facts.get("repo_root_reason", "git_metadata_unverified")},
            "repo_root": {"status": "passed" if source_facts.get("repo_root_ready") is True else "failed", "reason": source_facts.get("repo_root_reason", "repo_root_unverified"), "mode": source_facts.get("repo_root_mode", "")},
            "container_mount": {"status": "passed" if runner_facts.get("workspace_mount") or runner_facts.get("container_id") or getattr(run, "container_id", "") else "failed", "reason": "" if runner_facts.get("workspace_mount") or runner_facts.get("container_id") or getattr(run, "container_id", "") else "workspace_mount_unverified"},
            "model_preflight": {"status": "passed" if preflight.get("passed") is True else "failed", "reason": model_reason if preflight.get("passed") is not True else "", "repair_action": preflight.get("repair_action", "") or model_guidance.get("repair_action", ""), "repair_label": preflight.get("repair_label", "") or model_guidance.get("repair_label", "")},
        },
    })
    verified = report.get("passed") is True
    sealed = bool(artifact and artifact.status == "sealed")
    status = run.status
    if status == "patch_ready" and not (verified and sealed):
        status = "infrastructure_error"
    label, severity = TERMINAL_PRESENTATION.get(
        status,
        ("运行中" if status in {"pending", "running"} else "未知结果", "info"),
    )
    directly_adoptable = status == "patch_ready" and verified and sealed
    host_validate = ""
    if directly_adoptable and str(coding_runtime) == "claude_code":
        host_validate = "#!/usr/bin/env bash\nset -euo pipefail\ngit diff --check\ngit status --short\n"
    terminal = status not in {"pending", "running"}
    warning = ""
    if terminal and not directly_adoptable:
        warning = "该结果未形成完整的已验证 patch，不可直接采用。"
    raw_failure_reason = str(getattr(run, "failure_reason", "") or "")
    # Successful Code runs historically persist ``failure_reason=patch_ready``
    # as their terminal marker. Do not reinterpret that marker as an unknown
    # internal failure in the user-facing result.
    failure = (
        {}
        if status in {"patch_ready", "no_change_justified"}
        and raw_failure_reason in {"", status, "patch_ready"}
        else external_failure(raw_failure_reason)
    )
    return {
        "run_id": run.id,
        "project_id": run.project_id,
        "status": status,
        "label": label,
        "severity": severity,
        "terminal": terminal,
        "verified": verified,
        "sealed": sealed,
        "directly_adoptable": directly_adoptable,
        "warning": warning,
        "failure_reason": failure.get("reason", ""),
        "failure": failure,
        "manifest_id": getattr(run, "manifest_id", ""),
        "manifest_version": getattr(run, "manifest_version", 0),
        "verifier_report": report,
        "budget_usage": budget_usage,
        "runtime": runtime_summary,
        "host_validate": host_validate,
        "artifact": ({
            "id": artifact.id,
            "base_commit": artifact.base_commit,
            "diff_hash": artifact.diff_hash,
            "policy_hash": artifact.policy_hash,
            "verifier_report_hash": artifact.verifier_report_hash,
            "image": artifact.image,
            "image_id": artifact.image_id,
        } if artifact else None),
        "result_card": {
            "status": status,
            "label": label,
            "severity": severity,
            "summary": warning or label,
            "verified": verified,
            "directly_adoptable": directly_adoptable,
        },
    }


def format_patch_verification_output(result: dict) -> str:
    """Format the complete terminal result as safe Markdown for the chat.

    The control-plane result remains the machine-readable source of truth. The
    chat copy deliberately contains the same facts that used to be displayed
    in the standalone result card (including runtime and verifier evidence),
    while making clear that no commit or push is performed automatically.
    """
    if not isinstance(result, dict):
        result = {}

    def safe(value, limit=400):
        text = redact_code_output(str(value or "")).text
        text = text.encode("utf-8", errors="replace").decode("utf-8").strip()
        return text[:limit] + ("…" if len(text) > limit else "")

    def json_block(value, limit=12000):
        try:
            text = json.dumps(value if value is not None else {}, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            text = json.dumps(str(value), ensure_ascii=False)
        if len(text) > limit:
            text = text[:limit] + "\n…（证据已截断）"
        return text

    status = str(result.get("status") or "unknown")
    label = str(result.get("label") or status)
    report = result.get("verifier_report")
    report = report if isinstance(report, dict) else {}
    runtime = result.get("runtime")
    runtime = runtime if isinstance(runtime, dict) else {}
    readiness = runtime.get("readiness")
    readiness = readiness if isinstance(readiness, dict) else {}
    artifact = result.get("artifact")
    artifact = artifact if isinstance(artifact, dict) else {}
    failure = result.get("failure")
    failure = failure if isinstance(failure, dict) else {}
    paths = report.get("changed_paths") or report.get("changed_files") or []
    if not isinstance(paths, list):
        paths = []
    paths = [str(path) for path in paths if str(path).strip()][:20]
    verifier = "通过" if result.get("verified") is True else "未通过或证据不足"
    adoptable = "是" if result.get("directly_adoptable") is True else "否"
    # Keep the explicit warning in its own callout below; otherwise blocked
    # terminal results would show the same sentence twice.
    reason = safe(report.get("reason") or result.get("failure_reason") or "")
    failure_reason = safe(failure.get("reason") or result.get("failure_reason") or "")
    failure_stage = safe(failure.get("stage") or "")
    failure_detail = safe(failure.get("detail") or "", 800)
    runtime_name = str(runtime.get("coding_runtime") or "legacy")
    coding_runtime = safe(runtime_name, 120)
    preflight = runtime.get("preflight") if isinstance(runtime.get("preflight"), dict) else {}
    skills = runtime.get("skills") if isinstance(runtime.get("skills"), list) else []
    mcp_servers = runtime.get("mcp_servers") if isinstance(runtime.get("mcp_servers"), list) else []
    skill_names = [safe(item.get("name") if isinstance(item, dict) else item, 120) for item in skills[:20]]
    mcp_names = [safe(item.get("name") if isinstance(item, dict) else item, 120) for item in mcp_servers[:20]]
    code_snippets = _runtime_code_snippets(runtime)
    lines = [
    ]
    if code_snippets:
        lines.extend(["### 阶段代码片段", "> 以下内容来自 Claude Code 的 READ/EDIT/TEST 阶段，已脱敏并限制长度。"])
        for item in code_snippets:
            phase_label = {"file_changed": "EDIT", "tool_call": "READ/TOOL", "test_run": "TEST"}.get(item["phase"], item["phase"])
            target = item["path"] or item["command"] or "运行上下文"
            fence = "````" if "```" in item["snippet"] else "```"
            lines.extend([
                "",
                f"<details><summary>{phase_label} · {safe(target, 300)}</summary>",
                "",
                f"{fence}text",
                item["snippet"],
                fence,
                "",
                "</details>",
            ])
        lines.append("")
    lines.extend([
        "## Patch 验证结果",
        f"- Code run：`{safe(result.get('run_id'), 120) or '-'}`",
        f"- Manifest：v{result.get('manifest_version') or '-'}（{safe(result.get('manifest_id'), 120) or '-'}）",
        f"- Runtime：{coding_runtime}",
        f"- 执行状态：{label}（{status}）",
        f"- Verifier：{verifier}",
        f"- Sealed artifact：{'已生成' if result.get('sealed') else '不可用'}",
        f"- 可直接采用：{adoptable}",
        "- Git 提交/推送：未执行（如需执行，请在下一条消息明确要求）",
    ])
    if paths:
        lines.extend(["", "### 变更文件", *[f"- `{safe(path, 300)}`" for path in paths]])
    else:
        lines.extend(["", "### 变更文件", "- 无"])
    if reason:
        lines.extend(["", f"**说明：** {reason}"])
    if failure_reason or failure_stage or failure_detail:
        failure_line = " · ".join(part for part in (
            f"Failure {failure_reason}" if failure_reason else "",
            f"Stage {failure_stage}" if failure_stage else "",
            failure_detail,
        ) if part)
        if failure_line:
            lines.extend(["", f"**失败信息：** {failure_line}"])
    if runtime and (
        runtime_name == "claude_code"
        or runtime.get("preflight")
        or runtime.get("runtime_result")
        or runtime.get("skills")
        or runtime.get("mcp_servers")
    ):
        lines.extend([
            "",
            "### Claude Code Runtime",
            f"- Preflight：{'通过' if preflight.get('passed') is True else '未通过或不可用'}",
            f"- Skills：{len(skills)}" + (f"（{', '.join(skill_names)}）" if skill_names else ""),
            f"- MCP：{len(mcp_servers)}" + (f"（{', '.join(mcp_names)}）" if mcp_names else ""),
        ])
        for key, title in (
            ("workspace_files", "Workspace 文件"),
            ("git_metadata", "Git 元数据"),
            ("repo_root", "仓库根目录"),
            ("container_mount", "容器挂载"),
            ("model_preflight", "模型预检"),
        ):
            fact = readiness.get(key)
            if isinstance(fact, dict):
                state = "通过" if fact.get("status") == "passed" else "失败"
                detail = safe(fact.get("reason") or fact.get("mode") or "", 300)
                lines.append(f"- {title}：{state}" + (f"（{detail}）" if detail else ""))
        lines.extend(["", "<details>", "<summary>Runtime 证据</summary>", "", "```json", json_block(runtime), "```", "", "</details>"])
    if artifact:
        lines.extend([
            "",
            "### Sealed artifact",
            f"- Base：`{safe(artifact.get('base_commit'), 120) or '-'}`",
            f"- Diff：`{safe(artifact.get('diff_hash'), 120) or '-'}`",
            f"- Policy：`{safe(artifact.get('policy_hash'), 120) or '-'}`",
            f"- Verifier report hash：`{safe(artifact.get('verifier_report_hash'), 120) or '-'}`",
        ])
    # Verifier reports may contain scanner excerpts; redact strings before
    # copying them into a conversational channel.
    lines.extend(["", "<details>", "<summary>Verifier 证据</summary>", "", "```json", json_block(_redact_runtime_value(report)), "```", "", "</details>"])
    host_validate = str(result.get("host_validate") or "").strip()
    if host_validate:
        lines.extend(["", "### Host 验证命令", "```bash", host_validate, "```"])
    if result.get("warning"):
        lines.extend(["", f"> {safe(result.get('warning'), 500)}"])
    return "\n".join(lines)

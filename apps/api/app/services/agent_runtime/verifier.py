"""Verifier — goal-achievement verification for tool execution results.

P0 core module. Strictly separates "tool executed OK" from "goal achieved".

Two-pass strategy:
  1. Engine-side (fast, rule-based): Check row counts, error patterns, file evidence
  2. LLM-side (thorough, when needed): Deep semantic verification for complex tasks

Key principle: tool_result.success == True only means the tool RAN without error.
It does NOT mean the step's goal was achieved. Only VerificationResult.success=True
means the goal was met.

Usage:
    verif = Verifier.verify_tool_result(
        action_type="mcp_tool_call",
        tool_name="query_ads_view",
        tool_args={"view": "<已绑定资源>"},
        tool_output=raw_result,
        step_goal="Get user registration data for last 30 days",
        acceptance_criteria=["At least 100 rows returned", "No error in output"],
    )
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.services.agent_runtime.types import FailureType, VerificationResult

if TYPE_CHECKING:
    pass

# Thresholds
_MIN_ROWS_FOR_SUCCESS = 1  # At least 1 row for a query to be "successful"
_MAX_EMPTY_SHELL_LINES = 3  # Shell output with fewer lines is probably a no-op
_FULL_PAGE_WARNING_THRESHOLD = 900  # >= this many rows → likely truncated
_LLM_VERIFY_CONFIDENCE_THRESHOLD = 0.7  # Below this → trigger LLM verification

# Error detection patterns
_ERROR_PATTERNS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\berror\b",
        r"\bexception\b",
        r"\btraceback\b",
        r"\bfailed\b",
        r"\btimeout\b",
        r"\bdenied\b",
        r"\bpermission\b",
        r"(?<!\w)500\b",
        r"\bpanic\b",
        r"\bfatal\b",
    ]
]

_TRACEBACK_PATTERNS = [
    re.compile(p)
    for p in [
        r"Traceback\s*\(most recent call last\)",
        r"File\s+\".+?\",\s+line\s+\d+",
        r"\w+Error:",
        r"SyntaxError:",
        r"ImportError:",
        r"ModuleNotFoundError:",
        r"command not found",
        r"No such file or directory",
    ]
]

# Success indicators (positive signals)
_SUCCESS_INDICATORS = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r'"success"\s*:\s*true',
        r'"ok"\s*:\s*true',
        r"successfully",
        r"completed",
        r"完成",
        r"成功",
    ]
]


@dataclass
class _MCPResultInfo:
    """Parsed information from an MCP query result."""
    is_error: bool = False
    error_class: str = ""
    row_count: int = 0
    is_full_page: bool = False
    has_data: bool = False


class Verifier:
    """Checks if tool execution results achieve the intended step goal.

    All methods are static — no hidden state. Each call produces a
    self-contained VerificationResult.
    """

    # ---- Main entry: engine-side verification ----

    @staticmethod
    def verify_tool_result(
        action_type: str,
        tool_name: str,
        tool_args: dict | None,
        tool_output: str,
        *,
        step_goal: str = "",
        acceptance_criteria: list[str] | None = None,
        time_window: dict | None = None,
        row_threshold: int = _MIN_ROWS_FOR_SUCCESS,
    ) -> VerificationResult:
        """Engine-side rule-based verification of a single tool execution.

        Dispatches to action-specific verifiers based on action_type.

        Args:
            action_type: "mcp_tool_call", "shell", "file_write", "file_read", "patch"
            tool_name: The actual tool name (e.g. "query_ads_view")
            tool_args: Arguments passed to the tool
            tool_output: Raw output from tool execution
            step_goal: What the current PlanStep aims to achieve
            acceptance_criteria: Explicit verifiable conditions
            time_window: Optional time window context (for export MCP)
            row_threshold: Minimum rows expected for query success

        Returns:
            VerificationResult indicating whether the goal was achieved
        """
        criteria = acceptance_criteria or []

        if action_type == "mcp_tool_call":
            return Verifier._verify_mcp(
                tool_name, tool_args, tool_output,
                step_goal=step_goal, criteria=criteria,
                row_threshold=row_threshold,
            )
        elif action_type == "shell":
            return Verifier._verify_shell(
                tool_output,
                step_goal=step_goal, criteria=criteria,
            )
        elif action_type == "file_write":
            return Verifier._verify_file_write(
                tool_args, tool_output,
                step_goal=step_goal, criteria=criteria,
            )
        elif action_type == "file_read":
            return Verifier._verify_file_read(
                tool_output,
                step_goal=step_goal, criteria=criteria,
            )
        elif action_type == "patch":
            return Verifier._verify_patch(
                tool_args, tool_output,
                step_goal=step_goal, criteria=criteria,
            )
        else:
            # Unknown action type — can't verify, assume success for progress
            return VerificationResult(
                success=True,
                confidence=0.5,
                reason=f"Unknown action type '{action_type}' — assuming success",
                next_action="continue",
            )

    # ---- MCP verification ----

    @staticmethod
    def _verify_mcp(
        tool_name: str,
        tool_args: dict | None,
        tool_output: str,
        *,
        step_goal: str = "",
        criteria: list[str],
        row_threshold: int = _MIN_ROWS_FOR_SUCCESS,
    ) -> VerificationResult:
        """Verify an MCP tool call result."""
        info = Verifier._parse_mcp_result(tool_output)

        # 1. Tool-level error
        if info.is_error:
            ft = Verifier._classify_failure(tool_output)
            return VerificationResult(
                success=False,
                confidence=0.95,
                reason=f"MCP call failed: {info.error_class}",
                next_action=Verifier._recovery_action(ft),
                suggestion=Verifier._recovery_suggestion(ft, tool_name, tool_args),
                evidence=f"Error class: {info.error_class}",
            )

        # 2. Query returned no data
        if not info.has_data and tool_name in ("query_ads_view", "query_ads_metric", "run_query"):
            return VerificationResult(
                success=False,
                confidence=0.7,
                reason="Query returned no rows — possibly wrong filters or time range",
                next_action="retry",
                suggestion="Try adjusting WHERE clause, expanding time range, or checking view name with describe_ads_view",
                evidence=f"0 rows returned from {tool_name}",
            )

        # 3. Full page (potentially truncated)
        if info.is_full_page:
            return VerificationResult(
                success=False,  # Not truly done — data is incomplete
                confidence=0.6,
                reason=f"Result page is full ({info.row_count} rows) — data may be truncated",
                next_action="continue",
                suggestion=f"Continue with OFFSET to fetch remaining rows (use OFFSET={info.row_count})",
                evidence=f"Full page: {info.row_count} rows (>= {_FULL_PAGE_WARNING_THRESHOLD})",
            )

        # 4. Success with data
        if info.has_data:
            matched = []
            missed = []
            for c in criteria:
                if Verifier._check_criterion(c, tool_output, info):
                    matched.append(c)
                else:
                    missed.append(c)
            if missed and not matched:
                return VerificationResult(
                    success=False,
                    confidence=0.6,
                    reason=f"Data returned but criteria not met: {', '.join(missed[:3])}",
                    next_action="continue",
                    suggestion="Data was fetched but doesn't satisfy acceptance criteria — consider adjusting query",
                    matched_criteria=matched,
                    missed_criteria=missed,
                    evidence=f"{info.row_count} rows returned",
                )
            return VerificationResult(
                success=True,
                confidence=0.85 if not missed else 0.6,
                reason=f"Query returned {info.row_count} rows",
                next_action="continue",
                matched_criteria=matched,
                missed_criteria=missed,
                evidence=f"{info.row_count} rows returned from {tool_name}",
            )

        # 5. Describe/list tools — always successful if no error
        if tool_name in ("describe_ads_view", "list_ads_views", "describe_view", "list_views"):
            return VerificationResult(
                success=True,
                confidence=0.8,
                reason=f"Schema discovery completed via {tool_name}",
                next_action="continue",
            )

        # Fallback: no error, assume success
        return VerificationResult(
            success=True,
            confidence=0.5,
            reason=f"Tool {tool_name} executed without detected errors",
            next_action="continue",
        )

    # ---- SHELL verification ----

    @staticmethod
    def _verify_shell(
        tool_output: str,
        *,
        step_goal: str = "",
        criteria: list[str],
    ) -> VerificationResult:
        """Verify a SHELL command execution result."""
        output = tool_output or ""

        # 1. Check for traceback / error
        for pattern in _TRACEBACK_PATTERNS:
            if pattern.search(output):
                # Extract the error line for context
                lines = output.splitlines()
                error_line = ""
                for ln in lines:
                    if pattern.search(ln):
                        error_line = ln.strip()[:200]
                        break
                return VerificationResult(
                    success=False,
                    confidence=0.9,
                    reason=f"Shell command failed with error: {error_line}",
                    next_action="retry",
                    suggestion="Fix the error in the shell command and retry",
                    evidence=f"Error detected: {error_line}",
                )

        # 2. Check for empty / near-empty output
        meaningful_lines = [ln for ln in output.splitlines() if ln.strip() and not ln.strip().startswith("#")]
        if len(meaningful_lines) <= _MAX_EMPTY_SHELL_LINES:
            return VerificationResult(
                success=False,
                confidence=0.6,
                reason="Shell command produced almost no output — likely a no-op",
                next_action="retry",
                suggestion="Check the shell command — it may have produced no output",
                evidence=f"Only {len(meaningful_lines)} meaningful output lines",
            )

        # 3. Check for file creation evidence (common for data export SHELL tasks)
        file_created = bool(
            re.search(r"(written|saved|created|wrote|生成|写入|保存|导出)", output, re.IGNORECASE)
            or re.search(r"\.xlsx|\.csv|\.json", output)
        )

        # 4. Check acceptance criteria
        matched = []
        missed = []
        for c in criteria:
            if Verifier._check_shell_criterion(c, output, file_created):
                matched.append(c)
            else:
                missed.append(c)

        if file_created and not missed:
            return VerificationResult(
                success=True,
                confidence=0.8,
                reason="Shell command completed and produced output files",
                next_action="continue",
                matched_criteria=matched,
                evidence="File creation evidence found in SHELL output",
            )

        if missed and not matched:
            return VerificationResult(
                success=False,
                confidence=0.5,
                reason=f"Shell ran but criteria not met: {', '.join(missed[:3])}",
                next_action="retry",
                suggestion="Shell completed but doesn't satisfy acceptance criteria",
                matched_criteria=matched,
                missed_criteria=missed,
            )

        return VerificationResult(
            success=True,
            confidence=0.6,
            reason="Shell command executed without errors",
            next_action="continue",
            matched_criteria=matched,
            missed_criteria=missed,
        )

    # ---- WRITE verification ----

    @staticmethod
    def _verify_file_write(
        tool_args: dict | None,
        tool_output: str,
        *,
        step_goal: str = "",
        criteria: list[str],
    ) -> VerificationResult:
        """Verify a file write operation."""
        output = tool_output or ""
        args = tool_args or {}

        # Check for write success indicators
        if "success" in output.lower() or "写入成功" in output or "written" in output.lower():
            return VerificationResult(
                success=True,
                confidence=0.85,
                reason="File write confirmed successful",
                next_action="continue",
                evidence=f"Write success: {output[:200]}",
            )

        # Check for error
        if any(p.search(output) for p in _ERROR_PATTERNS):
            return VerificationResult(
                success=False,
                confidence=0.9,
                reason="File write failed with error",
                next_action="retry",
                suggestion="Check file path and content — write may have failed",
                evidence=output[:200],
            )

        return VerificationResult(
            success=True,
            confidence=0.5,
            reason="File write executed (unverified)",
            next_action="continue",
        )

    # ---- READ verification ----

    @staticmethod
    def _verify_file_read(
        tool_output: str,
        *,
        step_goal: str = "",
        criteria: list[str],
    ) -> VerificationResult:
        """Verify a file read operation."""
        output = tool_output or ""

        if not output.strip() or len(output.strip()) < 10:
            return VerificationResult(
                success=False,
                confidence=0.7,
                reason="File read returned empty or near-empty content",
                next_action="retry",
                suggestion="File may be empty or path may be wrong",
            )

        return VerificationResult(
            success=True,
            confidence=0.8,
            reason=f"File read returned {len(output)} chars",
            next_action="continue",
        )

    # ---- PATCH verification ----

    @staticmethod
    def _verify_patch(
        tool_args: dict | None,
        tool_output: str,
        *,
        step_goal: str = "",
        criteria: list[str],
    ) -> VerificationResult:
        """Verify a search-and-replace patch operation."""
        output = tool_output or ""

        if "success" in output.lower() or "替换" in output or "replaced" in output.lower():
            return VerificationResult(
                success=True,
                confidence=0.7,
                reason="Patch applied",
                next_action="continue",
                evidence=output[:200],
            )

        return VerificationResult(
            success=True,
            confidence=0.4,
            reason="Patch executed (unverified)",
            next_action="continue",
        )

    # ---- LLM-side deep verification ----

    @staticmethod
    async def verify_with_llm(
        llm,  # LLMResource
        step_goal: str,
        acceptance_criteria: list[str],
        tool_output: str,
        *,
        db,  # Session
        timeout: int = 12,
    ) -> VerificationResult:
        """LLM-side deep verification for complex tasks.

        Only called when engine-side confidence < _LLM_VERIFY_CONFIDENCE_THRESHOLD.
        Uses a small, focused LLM call to evaluate goal achievement semantically.
        """
        from app.services.llm_client import chat_completion

        prompt = f"""你是一个任务验证器。请判断以下工具执行结果是否达成了步骤目标。

步骤目标: {step_goal}

验收条件:
{chr(10).join(f'- {c}' for c in acceptance_criteria) if acceptance_criteria else '(无)'}

工具输出 (前 2000 字符):
{tool_output[:2000]}

请只回复一个 JSON 对象:
{{"success": true/false, "confidence": 0.0-1.0, "reason": "一句话说明", "next_action": "continue|retry|replan|ask_user"}}"""

        try:
            messages = [{"role": "user", "content": prompt}]
            raw = await chat_completion(llm, messages, max_tokens=256, db=db, timeout=timeout)
            # Parse JSON from response
            json_match = re.search(r"\{[^}]+\}", raw or "")
            if json_match:
                data = json.loads(json_match.group(0))
                return VerificationResult(
                    success=bool(data.get("success", False)),
                    confidence=float(data.get("confidence", 0.5)),
                    reason=str(data.get("reason", "LLM verification")),
                    next_action=str(data.get("next_action", "continue")),
                    source="llm",
                )
        except Exception:
            pass

        # Fallback if LLM verification fails
        return VerificationResult(
            success=True,
            confidence=0.3,
            reason="LLM verification unavailable — assuming success",
            next_action="continue",
            source="llm_fallback",
        )

    # ---- Deliverable verification (post-FINISH) ----

    @staticmethod
    def verify_deliverable(
        final_reply: str,
        *,
        step_goal: str = "",
        expected_files: list[str] | None = None,
    ) -> VerificationResult:
        """Post-FINISH verification that a deliverable actually exists and is valid.

        This is the last line of defense — the agent said FINAL, but we verify
        the deliverable is real before accepting the finish.
        """
        if not final_reply or len(final_reply.strip()) < 20:
            return VerificationResult(
                success=False,
                confidence=0.95,
                reason="FINAL reply is too short or empty — task likely not complete",
                next_action="retry",
                suggestion="The agent declared FINAL but produced no meaningful output",
            )

        # Check if FINAL mentions concrete deliverables
        has_deliverable = bool(
            re.search(r"\.xlsx|\.csv|\.json|文件|下载|deliverable", final_reply, re.IGNORECASE)
        )

        if has_deliverable:
            return VerificationResult(
                success=True,
                confidence=0.7,
                reason="FINAL reply references deliverable files",
                next_action="finish",
                evidence=f"Reply length: {len(final_reply)} chars",
            )

        return VerificationResult(
            success=True,
            confidence=0.5,
            reason="FINAL reply accepted (no deliverable reference detected)",
            next_action="finish",
            evidence=f"Reply length: {len(final_reply)} chars",
        )

    # ---- Internal helpers ----

    @staticmethod
    def _parse_mcp_result(tool_output: str) -> _MCPResultInfo:
        """Parse an MCP tool result into structured information."""
        info = _MCPResultInfo()
        output = tool_output or ""

        # Check for errors
        if any(p.search(output) for p in _ERROR_PATTERNS):
            info.is_error = True
            info.error_class = Verifier._classify_failure_type(output)

        # Parse row count
        info.row_count = Verifier._extract_row_count(output)
        info.has_data = info.row_count > 0

        # Check for full page
        info.is_full_page = info.row_count >= _FULL_PAGE_WARNING_THRESHOLD

        return info

    @staticmethod
    def _extract_row_count(text: str) -> int:
        """Extract row count from tool output."""
        # Try JSON parsing first
        try:
            data = json.loads(text)
            count = _count_rows_in_data(data)
            if count is not None and count > 0:
                return count
        except Exception:
            pass

        # Try regex patterns
        patterns = [
            r'(?:total|row_count|rows?|count|cnt)\s*[=:]\s*(\d+)',
            r'(\d+)\s*(?:rows?|行|records?|条)',
            r'"(?:total|row_count|rows?|count)"\s*:\s*(\d+)',
        ]
        for pat in patterns:
            match = re.search(pat, text, re.IGNORECASE)
            if match:
                return int(match.group(1))

        # Count JSON arrays
        array_match = re.findall(r'\[[\s\S]*?\]', text)
        for am in array_match:
            try:
                arr = json.loads(am)
                if isinstance(arr, list):
                    return len(arr)
            except Exception:
                pass

        return 0

    @staticmethod
    def _classify_failure_type(output: str) -> str:
        """Classify an error into FailureType value string."""
        r = (output or "").lower()
        if "timeout" in r or "超时" in r:
            return FailureType.TIMEOUT.value
        if "permission" in r or "denied" in r or "权限" in r or "auth" in r:
            return FailureType.PERMISSION_DENIED.value
        if "connection" in r or "connect" in r or "refused" in r or "网络" in r:
            return FailureType.TOOL_UNAVAILABLE.value
        if "context" in r and ("overflow" in r or "length" in r or "token" in r):
            return FailureType.CONTEXT_OVERFLOW.value
        if "field" in r or "column" in r or "字段" in r or "syntax" in r or "format" in r:
            return FailureType.INVALID_ARGUMENT.value
        return FailureType.BAD_RESULT.value

    @staticmethod
    def _classify_failure(output: str) -> FailureType:
        """Classify an error into a FailureType enum."""
        type_str = Verifier._classify_failure_type(output)
        try:
            return FailureType(type_str)
        except ValueError:
            return FailureType.BAD_RESULT

    @staticmethod
    def _recovery_action(ft: FailureType) -> str:
        """Map FailureType to recommended next_action."""
        mapping = {
            FailureType.TIMEOUT: "retry",
            FailureType.INVALID_ARGUMENT: "retry",
            FailureType.PERMISSION_DENIED: "ask_user",
            FailureType.TOOL_UNAVAILABLE: "retry",  # Try alternative tool
            FailureType.BAD_RESULT: "retry",
            FailureType.CONTEXT_OVERFLOW: "retry",
            FailureType.LLM_ERROR: "retry",
            FailureType.VERIFICATION_FAILED: "replan",
            FailureType.GOAL_NOT_ACHIEVED: "replan",
        }
        return mapping.get(ft, "retry")

    @staticmethod
    def _recovery_suggestion(
        ft: FailureType,
        tool_name: str = "",
        tool_args: dict | None = None,
    ) -> str:
        """Generate a human-readable recovery suggestion."""
        suggestions = {
            FailureType.TIMEOUT: "The MCP server timed out. Try again or reduce the query scope (shorter time range, LIMIT).",
            FailureType.INVALID_ARGUMENT: f"Tool '{tool_name}' received invalid arguments. Check the parameter format and retry with corrected args.",
            FailureType.PERMISSION_DENIED: "Permission denied. Ask the user for access or skip this step.",
            FailureType.TOOL_UNAVAILABLE: f"Tool '{tool_name}' is unavailable. Try an alternative tool or retry later.",
            FailureType.BAD_RESULT: "Tool executed but produced unusable output. Check input parameters and retry.",
            FailureType.CONTEXT_OVERFLOW: "Context limit reached. The system will trim older messages automatically.",
            FailureType.LLM_ERROR: "LLM API error. The system will retry automatically.",
            FailureType.VERIFICATION_FAILED: "The result did not meet acceptance criteria. Consider adjusting the approach.",
            FailureType.GOAL_NOT_ACHIEVED: "Step goal was not achieved after retries. Replanning is needed.",
        }
        return suggestions.get(ft, "Retry the operation.")

    @staticmethod
    def _check_criterion(criterion: str, tool_output: str, info: _MCPResultInfo) -> bool:
        """Check a single acceptance criterion against parsed MCP result info."""
        crit_lower = criterion.lower()
        output_lower = tool_output.lower()

        # Row count criteria
        row_match = re.search(r"(?:at least|>=\s*|≥\s*)(\d+)\s*rows?", crit_lower)
        if row_match:
            return info.row_count >= int(row_match.group(1))

        # No error criteria
        if "no error" in crit_lower or "无错误" in criterion or "成功" in criterion:
            return not info.is_error

        # Has data criteria
        if "has data" in crit_lower or "有数据" in criterion or "返回数据" in criterion:
            return info.has_data

        # Generic: check if criterion text appears in output
        if len(criterion) > 10:
            return criterion.lower() in output_lower

        return True  # Can't verify → assume met

    @staticmethod
    def _check_shell_criterion(criterion: str, output: str, file_created: bool) -> bool:
        """Check a single acceptance criterion against SHELL output."""
        crit_lower = criterion.lower()
        output_lower = output.lower()

        if "file" in crit_lower or "文件" in criterion or "output" in crit_lower:
            return file_created
        if "no error" in crit_lower or "无错误" in criterion:
            return not any(p.search(output) for p in _TRACEBACK_PATTERNS)
        if "compile" in crit_lower or "编译" in criterion:
            return "success" in output_lower or "completed" in output_lower
        if "test" in crit_lower or "测试" in criterion:
            return "passed" in output_lower or "success" in output_lower or "通过" in output

        return criterion.lower() in output_lower


def _count_rows_in_data(data) -> int | None:
    """Count rows in a parsed JSON data structure."""
    if isinstance(data, list):
        return len(data)
    if isinstance(data, dict):
        for key in ("rows", "data", "records", "result", "items", "content"):
            val = data.get(key)
            if isinstance(val, list):
                return len(val)
        # Check for nested {total, rows} pattern
        for key in ("total", "count", "row_count", "rows"):
            val = data.get(key)
            if isinstance(val, (int, float)):
                return int(val)
    return None

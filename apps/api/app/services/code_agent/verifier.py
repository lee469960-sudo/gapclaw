"""Authoritative post-loop verification for a frozen CodeAgent run."""

from __future__ import annotations

import fnmatch
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from app.services.code_agent.output_security import redact_code_output, scan_changed_content
from app.services.code_agent.budget import update_budget_usage
from app.services.code_agent.runner import RunnerCommandTimeout
from app.services.code_agent.scanner import _patch_secret_policy_action
from app.services.code_agent.workspace import (
    WorkspaceIntegrityError,
    WorkspaceIntegrityGuard,
    workspace_changed_paths,
)


@dataclass(frozen=True)
class VerificationReport:
    passed: bool
    outcome: str
    reason: str
    changed_paths: tuple[str, ...]
    checks: tuple[str, ...]
    tests: tuple[dict, ...]
    secret_scan_complete: bool = False
    content_hash: str = ""

    def to_dict(self) -> dict:
        value = asdict(self)
        value["changed_paths"] = list(self.changed_paths)
        value["checks"] = list(self.checks)
        value["tests"] = list(self.tests)
        return value


def _matches(path: str, rules: list[str]) -> bool:
    for raw in rules:
        rule = str(raw).replace("\\", "/").strip().strip("/")
        if not rule:
            continue
        if path == rule or path.startswith(rule + "/") or fnmatch.fnmatch(path, rule):
            return True
    return False


class CodeVerifier:
    def __init__(self, db, run, runner):
        self.db = db
        self.run = run
        self.runner = runner
        self.workspace = Path(run.workspace_path).resolve()
        self.policy = json.loads(run.effective_policy or "{}")
        self.contract = json.loads(run.task_contract or "{}")

    def _validation_plan(self) -> list[tuple[int, str]]:
        plan: list[tuple[int, str]] = []
        for index, item in enumerate(self.contract.get("validation_plan") or []):
            command = str(item.get("command") or "").strip() if isinstance(item, dict) else ""
            if not command:
                raise ValueError("verification_plan_invalid")
            plan.append((index, command))
        if not plan:
            raise ValueError("verification_plan_invalid")
        return plan

    def capture_baseline(self, workspace_manager) -> dict:
        """Run validation on the frozen commit, persist facts, then restore source."""
        tests: list[dict] = []
        baseline = {"status": "unavailable", "reason": "baseline_unavailable", "tests": tests}
        try:
            for index, command in self._validation_plan():
                exit_code, output = self.runner.exec(
                    self.run.container_id,
                    command,
                    timeout_seconds=int((self.policy.get("budgets") or {}).get("timeout_seconds") or 1800),
                )
                tests.append({
                    "test_index": index,
                    "command": command,
                    "exit_code": exit_code,
                    "output": redact_code_output(output).text,
                })
            baseline = {
                "status": "broken" if any(item["exit_code"] != 0 for item in tests) else "passed",
                "reason": "pre_existing_failure" if any(item["exit_code"] != 0 for item in tests) else "",
                "tests": tests,
            }
        except RunnerCommandTimeout:
            raise
        except Exception:
            baseline = {"status": "unavailable", "reason": "baseline_unavailable", "tests": tests}
        try:
            workspace_manager.reset_to_frozen_baseline(self.run)
        except Exception:
            baseline = {"status": "unavailable", "reason": "baseline_restore_failed", "tests": tests}
        self.run.verification_baseline = json.dumps(baseline, sort_keys=True)
        self.db.commit()
        return baseline

    def _changed_paths(self) -> tuple[str, ...]:
        return workspace_changed_paths(self.run)

    def _report(
        self,
        *,
        passed: bool,
        outcome: str,
        reason: str,
        changed: tuple[str, ...],
        checks: list[str],
        tests: list[dict],
        secret_scan_complete: bool = False,
        content_hash: str = "",
    ) -> VerificationReport:
        report = VerificationReport(
            passed, outcome, reason, changed, tuple(checks), tuple(tests),
            secret_scan_complete, content_hash,
        )
        self.run.verifier_report = json.dumps(report.to_dict(), sort_keys=True)
        self.db.commit()
        return report

    def verify(self) -> VerificationReport:
        checks: list[str] = []
        tests: list[dict] = []
        changed: tuple[str, ...] = ()
        try:
            WorkspaceIntegrityGuard(self.run, self.db).check_before_seal()
            checks.append("workspace_integrity")
            changed = self._changed_paths()
            changed_limit = int((self.policy.get("budgets") or {}).get("max_changed_files") or 20)
            update_budget_usage(
                self.db, self.run,
                changed_files=len(changed),
                max_changed_files=changed_limit,
            )
            if len(changed) > changed_limit:
                return self._report(
                    passed=False, outcome="budget_exhausted",
                    reason="changed_files_budget_exhausted",
                    changed=changed, checks=checks, tests=tests,
                )
            if any(_matches(path, self.policy.get("protected_paths") or []) for path in changed):
                return self._report(
                    passed=False, outcome="policy_rejected", reason="protected_path_modified",
                    changed=changed, checks=checks, tests=tests,
                )
            checks.append("protected_paths")
            if any(_matches(path, self.policy.get("test_integrity_paths") or []) for path in changed):
                return self._report(
                    passed=False, outcome="policy_rejected", reason="test_integrity_violation",
                    changed=changed, checks=checks, tests=tests,
                )
            checks.append("test_integrity")
            patch_secret_action = _patch_secret_policy_action(
                str(self.run.effective_policy or "")
            )
            initial_scan = scan_changed_content(self.workspace, changed)
            if initial_scan.detected and patch_secret_action != "warn":
                return self._report(
                    passed=False, outcome="policy_rejected", reason="secret_detected",
                    changed=changed, checks=checks, tests=tests,
                )
            if not initial_scan.complete:
                return self._report(
                    passed=False, outcome="verification_inconclusive",
                    reason=initial_scan.reason or "secret_scan_failed",
                    changed=changed, checks=checks, tests=tests,
                )
            try:
                baseline = json.loads(self.run.verification_baseline or "{}")
                baseline_tests = baseline.get("tests") if isinstance(baseline, dict) else None
            except json.JSONDecodeError:
                baseline, baseline_tests = {}, None
            plan = self._validation_plan()
            if (
                not isinstance(baseline_tests, list)
                or len(baseline_tests) != len(plan)
                or baseline.get("status") not in {"passed", "broken"}
            ):
                return self._report(
                    passed=False, outcome="verification_inconclusive",
                    reason="verification_baseline_unavailable", changed=changed,
                    checks=checks, tests=tests,
                )
            new_failure = False
            pre_existing_failure = False
            for index, command in plan:
                exit_code, output = self.runner.exec(
                    self.run.container_id,
                    command,
                    timeout_seconds=int((self.policy.get("budgets") or {}).get("timeout_seconds") or 1800),
                )
                safe_output = redact_code_output(output).text
                baseline_test = baseline_tests[index]
                baseline_exit = int(baseline_test.get("exit_code", 1))
                classification = "passed"
                if exit_code != 0:
                    if baseline_exit == 0:
                        classification = "new_failure"
                        new_failure = True
                    else:
                        classification = "pre_existing_failure"
                        pre_existing_failure = True
                tests.append({
                    "test_index": index,
                    "command": command,
                    "exit_code": exit_code,
                    "output": safe_output,
                    "failure_classification": classification,
                })
            checks.append("validation_plan")
            if new_failure:
                return self._report(
                    passed=False, outcome="verification_failed", reason="new_validation_failure",
                    changed=changed, checks=checks, tests=tests,
                )
            if pre_existing_failure:
                return self._report(
                    passed=False, outcome="baseline_broken", reason="pre_existing_failure",
                    changed=changed, checks=checks, tests=tests,
                )
            WorkspaceIntegrityGuard(self.run, self.db).check_before_seal()
            changed = self._changed_paths()
            update_budget_usage(
                self.db, self.run,
                changed_files=len(changed),
                max_changed_files=changed_limit,
            )
            if len(changed) > changed_limit:
                return self._report(
                    passed=False, outcome="budget_exhausted",
                    reason="changed_files_budget_exhausted",
                    changed=changed, checks=checks, tests=tests,
                )
            if any(_matches(path, self.policy.get("protected_paths") or []) for path in changed):
                return self._report(
                    passed=False, outcome="policy_rejected", reason="protected_path_modified",
                    changed=changed, checks=checks, tests=tests,
                )
            if any(_matches(path, self.policy.get("test_integrity_paths") or []) for path in changed):
                return self._report(
                    passed=False, outcome="policy_rejected", reason="test_integrity_violation",
                    changed=changed, checks=checks, tests=tests,
                )
            final_scan = scan_changed_content(self.workspace, changed)
            if final_scan.detected and patch_secret_action != "warn":
                return self._report(
                    passed=False, outcome="policy_rejected", reason="secret_detected",
                    changed=changed, checks=checks, tests=tests,
                )
            if not final_scan.complete:
                return self._report(
                    passed=False, outcome="verification_inconclusive",
                    reason=final_scan.reason or "secret_scan_failed",
                    changed=changed, checks=checks, tests=tests,
                )
            checks.append("secret_detection_complete")
            return self._report(
                passed=True, outcome="verification_passed", reason="",
                changed=changed, checks=checks, tests=tests,
                secret_scan_complete=True, content_hash=final_scan.content_hash,
            )
        except WorkspaceIntegrityError as exc:
            return self._report(
                passed=False, outcome="workspace_integrity_error", reason=exc.reason,
                changed=changed, checks=checks, tests=tests,
            )
        except RunnerCommandTimeout:
            raise
        except Exception:
            return self._report(
                passed=False, outcome="verification_inconclusive",
                reason="verifier_unavailable", changed=changed, checks=checks, tests=tests,
            )

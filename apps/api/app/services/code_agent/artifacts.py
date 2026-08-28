"""Atomic canonical patch sealing for verified CodeAgent workspaces."""

from __future__ import annotations

import hashlib
import io
import json
import os
import shutil
import subprocess
import tarfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from app.config import get_settings
from app.models import CodeArtifact, CodeScanReport, CodeSourceSnapshot
from app.security import new_id, now_str
from app.services.code_agent.budget import update_budget_usage
from app.services.code_agent.scanner import (
    BuiltinSecretScanner,
    persist_scan_report,
    persisted_scan_report_bytes,
    _patch_secret_policy_action,
    _source_scan_complete_or_warned,
    _source_secret_policy_action,
    _source_unscannable_policy_action,
)
from app.services.code_agent.workspace import (
    WorkspaceIntegrityGuard,
    flatten_archived_base_if_needed,
    is_runtime_only_path,
    workspace_changed_paths,
)


@dataclass(frozen=True)
class SealingResult:
    sealed: bool
    outcome: str
    reason: str
    artifact_id: str = ""
    diff_hash: str = ""


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


class CodeArtifactSealer:
    def __init__(
        self, db, run, runner, *, root: str | Path | None = None,
        patch_scanner=None,
    ):
        self.db = db
        self.run = run
        self.runner = runner
        configured = Path(get_settings().data_dir) / "code-artifacts"
        self.root = Path(root or configured).resolve()
        self.patch_scanner = patch_scanner or BuiltinSecretScanner(scope="patch")

    def _git(self, args: list[str], *, cwd: Path | None = None, binary: bool = False):
        env = dict(os.environ)
        env.update({
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_PAGER": "cat",
        })
        completed = subprocess.run(
            ["git", "-c", "core.hooksPath=/dev/null", *args],
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=not binary,
            timeout=60,
        )
        return completed

    @staticmethod
    def _normalize_diff_headers(diff: str) -> str:
        lines: list[str] = []
        replacements = {
            "diff --git a/base/": "diff --git a/",
            "--- a/base/": "--- a/",
            "+++ b/workspace/": "+++ b/",
            "+++ b/workspace-canonical/": "+++ b/",
            "rename from base/": "rename from ",
            "rename to workspace/": "rename to ",
            "rename to workspace-canonical/": "rename to ",
        }
        for line in diff.splitlines(keepends=True):
            for prefix, replacement in replacements.items():
                if line.startswith(prefix):
                    line = replacement + line[len(prefix):]
                    break
            if line.startswith("diff --git a/") and " b/workspace/" in line:
                line = line.replace(" b/workspace/", " b/", 1)
            if line.startswith("diff --git a/") and " b/workspace-canonical/" in line:
                line = line.replace(" b/workspace-canonical/", " b/", 1)
            lines.append(line)
        return "".join(lines)

    def _canonical_diff(self) -> bytes:
        workspace = Path(self.run.workspace_path).resolve()
        run_root = workspace.parent
        mirror = run_root / "source.git"
        base = run_root / "base"
        canonical_workspace = run_root / "workspace-canonical"
        if not workspace.is_dir() or not mirror.is_dir() or base.exists() or canonical_workspace.exists():
            raise RuntimeError("seal_source_unavailable")
        base.mkdir()
        try:
            archive = self._git(
                ["--git-dir", str(mirror), "archive", "--format=tar", self.run.base_commit],
                binary=True,
            )
            if archive.returncode != 0:
                raise RuntimeError("seal_base_archive_failed")
            with tarfile.open(fileobj=io.BytesIO(archive.stdout), mode="r:") as bundle:
                bundle.extractall(base, filter="data")
            flatten_archived_base_if_needed(self.run, base)
            shutil.copytree(
                workspace,
                canonical_workspace,
                ignore=lambda _dir, names: [
                    name for name in names if is_runtime_only_path(name)
                ],
            )
            diff = self._git([
                "diff", "--no-index", "--binary", "--full-index",
                "--no-ext-diff", "--no-textconv", "--", "base", "workspace-canonical",
            ], cwd=run_root)
            if diff.returncode not in {0, 1}:
                raise RuntimeError("seal_diff_failed")
            return self._normalize_diff_headers(diff.stdout).encode("utf-8")
        finally:
            shutil.rmtree(base, ignore_errors=True)
            shutil.rmtree(canonical_workspace, ignore_errors=True)

    def seal(self) -> SealingResult:
        temp: Path | None = None
        final: Path | None = None
        committed = False
        final_created = False
        try:
            if self.run.status in {"target_not_found", "needs_user_decision"}:
                return SealingResult(False, self.run.status, self.run.status)
            report = json.loads(self.run.verifier_report or "{}")
            if report.get("passed") is not True:
                return SealingResult(False, "infrastructure_error", "verifier_report_missing")
            if (
                report.get("secret_scan_complete") is not True
                or "secret_detection_complete" not in (report.get("checks") or [])
                or not str(report.get("content_hash") or "")
                or not isinstance(report.get("changed_paths"), list)
            ):
                return SealingResult(False, "infrastructure_error", "verifier_report_incomplete")
            source_scan = self.db.get(
                CodeScanReport, str(self.run.source_scan_report_id or "")
            )
            snapshot = self.db.get(
                CodeSourceSnapshot, str(self.run.snapshot_id or "")
            )
            policy_text = str(self.run.effective_policy or "")
            source_secret_action = _source_secret_policy_action(policy_text)
            source_unscannable_action = _source_unscannable_policy_action(policy_text)
            patch_secret_action = _patch_secret_policy_action(policy_text)
            if (
                source_scan is None
                or snapshot is None
                or source_scan.scope != "source"
                or not _source_scan_complete_or_warned(
                    source_scan,
                    source_unscannable_action=source_unscannable_action,
                )
                or source_scan.findings_count < 0
                or (source_scan.findings_count and source_secret_action != "warn")
                or snapshot.status != "sealed"
                or snapshot.scan_report_id != source_scan.id
                or snapshot.id != self.run.snapshot_id
                or snapshot.content_hash != self.run.snapshot_hash
                or snapshot.resolved_commit != self.run.resolved_commit
                or not self.run.image_digest
                or not self.run.effective_policy_hash
            ):
                return SealingResult(False, "infrastructure_error", "artifact_evidence_missing")
            self.runner.freeze(self.run.container_id)
            WorkspaceIntegrityGuard(self.run, self.db).check_before_seal()
            changed = workspace_changed_paths(self.run)
            if list(changed) != report["changed_paths"]:
                return SealingResult(False, "infrastructure_error", "verifier_content_mismatch")
            diff_bytes = self._canonical_diff()
            sealed_scan = self.patch_scanner.scan(
                self.run.workspace_path,
                changed,
                extra_inputs={"@canonical.patch.diff": diff_bytes},
            )
            scan_row = persist_scan_report(self.db, sealed_scan)
            if sealed_scan.findings_count and patch_secret_action != "warn":
                return SealingResult(False, "policy_rejected", "secret_detected")
            if not sealed_scan.complete:
                return SealingResult(
                    False, "infrastructure_error",
                    sealed_scan.failure_reason or "patch_scan_failed",
                )
            content_scan = BuiltinSecretScanner(scope="patch").scan(
                self.run.workspace_path, changed
            )
            if (
                (content_scan.findings and patch_secret_action != "warn")
                or not content_scan.complete
                or content_scan.input_hash != report["content_hash"]
            ):
                return SealingResult(False, "infrastructure_error", "verifier_content_mismatch")
            report["patch_scan_report_id"] = scan_row.id
            report["patch_scan_input_hash"] = scan_row.input_hash
            self.run.verifier_report = json.dumps(report, sort_keys=True)
            self.db.commit()
            diff_lines = diff_bytes.count(b"\n")
            policy = json.loads(self.run.effective_policy or "{}")
            diff_limit = int((policy.get("budgets") or {}).get("max_diff_lines") or 500)
            update_budget_usage(
                self.db, self.run,
                diff_lines=diff_lines,
                max_diff_lines=diff_limit,
            )
            if diff_lines > diff_limit:
                return SealingResult(
                    False, "budget_exhausted", "diff_lines_budget_exhausted"
                )
            policy_bytes = json.dumps(
                policy,
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
            policy_hash = _sha256(policy_bytes)
            if policy_hash != self.run.effective_policy_hash:
                return SealingResult(False, "infrastructure_error", "artifact_evidence_mismatch")
            report_bytes = json.dumps(report, sort_keys=True, separators=(",", ":")).encode()
            source_scan_bytes = persisted_scan_report_bytes(source_scan)
            patch_scan_bytes = persisted_scan_report_bytes(scan_row)
            runner_facts = json.loads(self.run.runner_facts or "{}")
            artifact_id = new_id()
            manifest = {
                "version": 2,
                "artifact_id": artifact_id,
                "run_id": self.run.id,
                "project_id": self.run.project_id,
                "manifest_version": self.run.manifest_version,
                "base_commit": self.run.base_commit,
                "resolved_commit": self.run.resolved_commit,
                "snapshot_id": self.run.snapshot_id,
                "snapshot_hash": self.run.snapshot_hash,
                "diff_hash": _sha256(diff_bytes),
                "policy_hash": policy_hash,
                "verifier_report_hash": _sha256(report_bytes),
                "source_scan_report_id": source_scan.id,
                "source_scan_report_hash": _sha256(source_scan_bytes),
                "patch_scan_report_id": scan_row.id,
                "patch_scan_report_hash": _sha256(patch_scan_bytes),
                "image": self.run.image,
                "image_digest": self.run.image_digest,
                "image_id": str(runner_facts.get("image_id") or ""),
            }
            manifest_bytes = json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
            self.root.mkdir(parents=True, exist_ok=True)
            temp = self.root / f".{self.run.id}.{uuid.uuid4().hex}.tmp"
            final = self.root / self.run.id
            if final.exists():
                raise RuntimeError("artifact_already_exists")
            temp.mkdir()
            (temp / "patch.diff").write_bytes(diff_bytes)
            (temp / "policy.json").write_bytes(policy_bytes)
            (temp / "verifier-report.json").write_bytes(report_bytes)
            (temp / "source-scan-report.json").write_bytes(source_scan_bytes)
            (temp / "patch-scan-report.json").write_bytes(patch_scan_bytes)
            (temp / "manifest.json").write_bytes(manifest_bytes)
            temp.rename(final)
            temp = None
            final_created = True
            for path in final.iterdir():
                path.chmod(0o400)
            final.chmod(0o500)
            artifact = CodeArtifact(
                id=artifact_id,
                run_id=self.run.id,
                project_id=self.run.project_id,
                manifest_version=self.run.manifest_version,
                base_commit=self.run.base_commit,
                diff_hash=manifest["diff_hash"],
                policy_hash=manifest["policy_hash"],
                verifier_report_hash=manifest["verifier_report_hash"],
                image=self.run.image,
                image_id=manifest["image_id"],
                storage_path=str(final),
                status="sealed",
                created_at=now_str(),
            )
            self.db.add(artifact)
            self.run.artifact_id = artifact_id
            self.run.workspace_state = "sealed"
            self.run.workspace_downloadable = False
            self.db.commit()
            committed = True
            outcome = "patch_ready" if diff_bytes else "no_change_justified"
            return SealingResult(True, outcome, "", artifact_id, manifest["diff_hash"])
        except Exception:
            self.db.rollback()
            if temp and temp.exists():
                shutil.rmtree(temp, ignore_errors=True)
            if final and final.exists() and final_created and not committed:
                for path in final.iterdir():
                    path.chmod(0o600)
                final.chmod(0o700)
                shutil.rmtree(final, ignore_errors=True)
            self.run.artifact_id = ""
            self.run.workspace_downloadable = False
            return SealingResult(False, "infrastructure_error", "artifact_sealing_failed")

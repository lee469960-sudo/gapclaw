"""OpenSpec task 7.1: immutable, coverage-bearing scanner reports."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

from app.services.code_agent.scanner import (
    BuiltinSecretScanner,
    scan_report_passes,
)


def test_complete_text_scan_records_coverage_and_redacted_findings(tmp_path):
    (tmp_path / "safe.txt").write_text("hello\n", encoding="utf-8")
    (tmp_path / "secret.txt").write_text(
        "API_KEY=sk-example0123456789abcdef\n", encoding="utf-8"
    )

    report = BuiltinSecretScanner(scope="source").scan(tmp_path)

    assert report.complete is True
    assert report.status == "complete"
    assert report.files_discovered == report.files_scanned == 2
    assert report.bytes_discovered == report.bytes_scanned
    assert report.skipped_count == report.truncated_count == 0
    assert report.findings_count == 1
    assert report.findings[0].path == "secret.txt"
    assert report.findings[0].classification == "secret_pattern"
    assert "sk-example" not in repr(report)
    assert scan_report_passes(report, report.input_hash) is False


def test_scan_report_is_immutable_and_hash_mismatch_never_passes(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    report = BuiltinSecretScanner(scope="patch").scan(tmp_path)

    assert scan_report_passes(report, report.input_hash) is True
    assert scan_report_passes(report, "0" * 64) is False
    try:
        report.complete = False
    except dataclasses.FrozenInstanceError:
        pass
    else:
        raise AssertionError("ScanReport must be immutable")


def test_binary_input_is_incomplete_and_never_passes(tmp_path):
    (tmp_path / "image.bin").write_bytes(b"header\0payload")

    report = BuiltinSecretScanner(scope="source").scan(tmp_path)

    assert report.complete is False
    assert report.status == "incomplete"
    assert report.skipped_count == 1
    assert report.failure_reason == "scanner_binary_unsupported"
    assert scan_report_passes(report, report.input_hash) is False


def test_large_file_is_truncated_and_never_passes(tmp_path):
    (tmp_path / "large.txt").write_text("x" * 32, encoding="utf-8")

    report = BuiltinSecretScanner(
        scope="source", max_file_bytes=16
    ).scan(tmp_path)

    assert report.complete is False
    assert report.truncated_count == 1
    assert report.files_scanned == 0
    assert report.failure_reason == "scanner_file_too_large"
    assert scan_report_passes(report, report.input_hash) is False


def test_unsupported_file_type_is_incomplete_and_never_passes(tmp_path, monkeypatch):
    path = tmp_path / "unsupported"
    path.write_text("value", encoding="utf-8")
    real_lstat = type(path).lstat

    def unsupported_lstat(candidate):
        value = real_lstat(candidate)
        if candidate == path:
            return SimpleNamespace(st_mode=0, st_size=value.st_size)
        return value

    monkeypatch.setattr(type(path), "lstat", unsupported_lstat)
    report = BuiltinSecretScanner(scope="source").scan(tmp_path)

    assert report.complete is False
    assert report.skipped_count == 1
    assert report.failure_reason == "scanner_unsupported_format"


def test_timeout_returns_failed_incomplete_report(tmp_path):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
    ticks = iter([0.0, 2.0])

    report = BuiltinSecretScanner(
        scope="source", timeout_seconds=1, clock=lambda: next(ticks)
    ).scan(tmp_path)

    assert report.complete is False
    assert report.status == "failed"
    assert report.failure_reason == "scanner_timeout"


def test_scanner_crash_returns_failed_incomplete_report(tmp_path, monkeypatch):
    (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")

    def crash(_path, _pattern):
        raise OSError("scanner backend unavailable")

    monkeypatch.setattr(type(tmp_path), "rglob", crash)
    report = BuiltinSecretScanner(scope="source").scan(tmp_path)

    assert report.complete is False
    assert report.status == "failed"
    assert report.failure_reason == "scanner_crashed"
    assert scan_report_passes(report, report.input_hash) is False

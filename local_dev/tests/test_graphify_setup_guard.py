"""graphify_setup_guard: 성공했지만 probe가 못 본 설치를 기억해 재질문을 막는다."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from local_dev.serena_mcp_management import graphify_setup_guard as guard


@pytest.fixture
def guard_file(tmp_path, monkeypatch):
    runtime_root = tmp_path / "runtime"
    monkeypatch.setenv("SERENA_AGENT_RUNTIME_ROOT", str(runtime_root))
    return runtime_root / guard.GUARD_FILE_NAME


def test_not_suppressed_before_anything_is_recorded(guard_file, tmp_path):
    assert not guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-1")


def test_record_then_suppress_same_project_component_version_and_fingerprint(
    guard_file, tmp_path,
):
    assert guard.record(tmp_path, "integration", "0.9.53", "fp-1")

    assert guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-1")
    assert guard_file.is_file()
    assert oct(guard_file.stat().st_mode & 0o777) == "0o600"


def test_suppression_is_scoped(guard_file, tmp_path):
    guard.record(tmp_path, "integration", "0.9.53", "fp-1")

    # 다른 파일 내용, 다른 graphify 버전, 다른 컴포넌트, 다른 프로젝트는 다시 묻는다.
    assert not guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-2")
    assert not guard.is_suppressed(tmp_path, "integration", "0.9.54", "fp-1")
    assert not guard.is_suppressed(tmp_path, "hook", "0.9.53", "fp-1")
    assert not guard.is_suppressed(tmp_path / "other", "integration", "0.9.53", "fp-1")


def test_unknown_version_is_a_valid_key(guard_file, tmp_path):
    guard.record(tmp_path, "hook", None, "fp-1")

    assert guard.is_suppressed(tmp_path, "hook", None, "fp-1")


def test_record_overwrites_previous_fingerprint_for_same_key(guard_file, tmp_path):
    guard.record(tmp_path, "integration", "0.9.53", "fp-1")
    guard.record(tmp_path, "integration", "0.9.53", "fp-2")

    assert guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-2")
    assert not guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-1")
    entries = json.loads(guard_file.read_text())
    assert len(entries) == 1


def test_corrupt_guard_file_is_treated_as_empty(guard_file, tmp_path):
    guard_file.parent.mkdir(parents=True, mode=0o700)
    guard_file.write_text("{oops")
    guard_file.chmod(0o600)  # our own 0600 file, its content corrupted

    assert not guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-1")
    assert guard.record(tmp_path, "integration", "0.9.53", "fp-1")
    assert guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-1")


def test_record_reports_failure_instead_of_raising(tmp_path, monkeypatch):
    monkeypatch.setenv("SERENA_AGENT_RUNTIME_ROOT", "relative/not/allowed")

    assert not guard.record(tmp_path, "integration", "0.9.53", "fp-1")
    assert not guard.is_suppressed(tmp_path, "integration", "0.9.53", "fp-1")

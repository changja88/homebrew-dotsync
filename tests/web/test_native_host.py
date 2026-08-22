from __future__ import annotations

import base64
import io
import json
import os
from pathlib import Path

import pytest

import dotsync.native_host as native_host_module
from dotsync.app_paths import AppPaths
from dotsync.app_state import AppState
from dotsync.native_host import (
    NativeHostHandshake,
    NativeHostProtocolError,
    run_native_host,
)
from dotsync.web import WebApplication


class _StateStore:
    def load(self) -> AppState:
        return AppState()

    def save(self, state: AppState) -> None:
        raise AssertionError("native transport must not save app state")


class _Jobs:
    def __init__(self) -> None:
        self.shutdown_calls = 0

    def list_jobs(self) -> list[object]:
        return []

    def shutdown(self) -> None:
        self.shutdown_calls += 1


def _application(tmp_path: Path, *, idle_shutdown_enabled: bool = False):
    jobs = _Jobs()
    application = WebApplication(
        paths=AppPaths(tmp_path / "app-data"),
        state_store=_StateStore(),
        account_store=object(),
        usage_service=object(),
        sync_service=None,
        folder_picker=lambda: None,
        sync_folder_initializer=lambda: None,
        reveal_app_data=lambda path: None,
        open_provider_url=lambda url: None,
        job_registry=jobs,
        idle_shutdown_enabled=idle_shutdown_enabled,
    )
    return application, jobs


def _reject_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def test_native_host_emits_one_bounded_handshake_and_stops_on_control_eof(tmp_path):
    application, jobs = _application(tmp_path)
    read_fd, write_fd = os.pipe()
    control = os.fdopen(read_fd, "rb", buffering=0)
    handshake = io.BytesIO()
    os.close(write_fd)

    result = run_native_host(
        application,
        control=control,
        handshake=handshake,
        poll_interval=0.01,
    )

    assert result == 0
    encoded = handshake.getvalue()
    assert encoded.endswith(b"\n")
    assert not encoded.endswith(b"\n\n")
    assert len(encoded) <= 4096
    lines = encoded.splitlines()
    assert len(lines) == 1
    payload = json.loads(lines[0], object_pairs_hook=_reject_duplicates)
    assert set(payload) == {"schema_version", "origin", "token"}
    assert payload["schema_version"] == 1
    assert payload["origin"].startswith("http://127.0.0.1:")
    assert len(base64.urlsafe_b64decode(payload["token"] + "=")) == 32
    assert jobs.shutdown_calls == 1


def test_native_host_returns_fixed_protocol_failure_for_control_bytes(
    tmp_path, capsys
):
    application, jobs = _application(tmp_path)
    read_fd, write_fd = os.pipe()
    control = os.fdopen(read_fd, "rb", buffering=0)
    handshake = io.BytesIO()
    os.write(write_fd, b"private-control-byte")

    try:
        result = run_native_host(
            application,
            control=control,
            handshake=handshake,
            poll_interval=0.01,
        )
    finally:
        os.close(write_fd)

    assert result == 2
    assert capsys.readouterr() == ("", "")
    assert jobs.shutdown_calls == 1


def test_native_host_rejects_browser_owned_idle_lifetime_before_start(tmp_path):
    application, jobs = _application(tmp_path, idle_shutdown_enabled=True)

    with pytest.raises(
        NativeHostProtocolError,
        match="native host requires parent-owned lifetime",
    ):
        run_native_host(
            application,
            control=io.BytesIO(),
            handshake=io.BytesIO(),
        )

    assert jobs.shutdown_calls == 0


def test_native_host_handshake_write_failure_closes_server_and_jobs(tmp_path):
    application, jobs = _application(tmp_path)
    read_fd, write_fd = os.pipe()
    control = os.fdopen(read_fd, "rb", buffering=0)

    class _FailingHandshake:
        def write(self, value: bytes) -> int:
            raise OSError("fixed-write-failure")

        def flush(self) -> None:
            raise AssertionError("flush must not follow a failed write")

    try:
        with pytest.raises(OSError, match="fixed-write-failure"):
            run_native_host(
                application,
                control=control,
                handshake=_FailingHandshake(),
                poll_interval=0.01,
            )
    finally:
        os.close(write_fd)

    assert jobs.shutdown_calls == 1


def test_native_host_returns_failure_when_server_dies_before_control_eof(
    tmp_path, monkeypatch
):
    application, jobs = _application(tmp_path)
    closed = []

    class _DeadServer:
        origin = "http://127.0.0.1:49152"

        def wait(self, *, timeout=None) -> bool:
            return True

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            closed.append(True)
            application.shutdown()

    monkeypatch.setattr(
        native_host_module,
        "run_ui_server",
        lambda application, poll_interval: _DeadServer(),
    )
    read_fd, write_fd = os.pipe()
    control = os.fdopen(read_fd, "rb", buffering=0)

    try:
        result = run_native_host(
            application,
            control=control,
            handshake=io.BytesIO(),
            poll_interval=0.01,
        )
    finally:
        os.close(write_fd)

    assert result == 1
    assert closed == [True]
    assert jobs.shutdown_calls == 1


@pytest.mark.parametrize(
    ("schema_version", "origin", "token"),
    [
        (True, "http://127.0.0.1:49152", "A" * 43),
        (1, "https://127.0.0.1:49152", "A" * 43),
        (1, "http://localhost:49152", "A" * 43),
        (1, "http://127.0.0.1:49152/?secret=yes", "A" * 43),
        (1, "http://127.0.0.1:49152", "secret token"),
    ],
)
def test_native_handshake_rejects_non_contract_values(
    schema_version, origin, token
):
    with pytest.raises(NativeHostProtocolError, match="native handshake is invalid"):
        NativeHostHandshake(
            schema_version=schema_version,
            origin=origin,
            token=token,
        )


def test_native_handshake_is_compact_ascii_lf_framed_and_hides_token_in_repr():
    token = "A" * 43
    value = NativeHostHandshake(
        schema_version=1,
        origin="http://127.0.0.1:49152",
        token=token,
    )

    encoded = value.encode_line()

    assert encoded == (
        b'{"schema_version":1,"origin":"http://127.0.0.1:49152",'
        b'"token":"AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"}\n'
    )
    assert token not in repr(value)

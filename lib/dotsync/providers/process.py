"""Sanitized process primitives for account-scoped provider commands."""

from __future__ import annotations

import codecs
import errno
import json
import os
import pty
import selectors
import shutil
import signal
import stat
import subprocess
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Callable

from dotsync.accounts import ProviderName

from .base import ProviderError


_PASSTHROUGH_VARIABLES = frozenset(
    {
        "PATH",
        "USER",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_ADDRESS",
        "LC_COLLATE",
        "LC_CTYPE",
        "LC_IDENTIFICATION",
        "LC_MEASUREMENT",
        "LC_MESSAGES",
        "LC_MONETARY",
        "LC_NAME",
        "LC_NUMERIC",
        "LC_PAPER",
        "LC_TELEPHONE",
        "LC_TIME",
        "TERM",
        "COLORTERM",
        "NO_COLOR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
        "CURL_CA_BUNDLE",
        "REQUESTS_CA_BUNDLE",
        "COMMAND_MODE",
        "SECURITYSESSIONID",
        "XPC_FLAGS",
        "XPC_SERVICE_NAME",
        "__CFBundleIdentifier",
        "__CF_USER_TEXT_ENCODING",
    }
)
_MAX_RPC_LINE_BYTES = 1024 * 1024
_MAX_PTY_OUTPUT_BYTES = 2 * 1024 * 1024
_MAX_PTY_INPUT_BYTES = 64 * 1024
_SAFE_REMOTE_ERROR_CODES = {
    -32601: "rpc_method_not_found",
    -32602: "rpc_invalid_params",
    -32001: "rpc_server_overloaded",
}


def provider_environment(
    provider: ProviderName | str,
    account_root: Path,
) -> dict[str, str]:
    """Build an allowlisted environment rooted in one managed account."""
    if provider not in {"claude", "codex"}:
        raise ValueError("unsupported provider")

    env = {
        key: value
        for key, value in os.environ.items()
        if key in _PASSTHROUGH_VARIABLES
    }
    home = account_root / "home"
    tmp = account_root / "tmp"
    env["TMPDIR"] = str(tmp)
    if provider == "claude":
        user_home = env.get("HOME") or str(Path.home())
        if not Path(user_home).is_absolute():
            raise ProviderError(
                "process_start_failed",
                "The macOS user home is unavailable.",
            )
        env["HOME"] = user_home
        env["CLAUDE_CONFIG_DIR"] = str(home)
        env["CLAUDE_SECURESTORAGE_CONFIG_DIR"] = str(home)
        env["CLAUDE_CODE_TMPDIR"] = str(tmp)
    else:
        env["HOME"] = str(home)
        env["CODEX_HOME"] = str(home)
    return env


def resolve_executable(command: str, *, path: str | None = None) -> Path:
    """Resolve a fixed command name to a verified absolute executable file."""
    if not command or Path(command).name != command:
        raise ProviderError(
            "executable_unavailable", "Provider executable is unavailable."
        )
    found = shutil.which(command, path=path)
    if found is None:
        raise ProviderError(
            "executable_unavailable", "Provider executable is unavailable."
        )
    resolved = Path(found).resolve()
    try:
        mode = resolved.stat().st_mode
    except OSError as error:
        raise ProviderError(
            "executable_unavailable", "Provider executable is unavailable."
        ) from error
    if not stat.S_ISREG(mode) or not os.access(resolved, os.X_OK):
        raise ProviderError(
            "executable_unavailable", "Provider executable is unavailable."
        )
    return resolved


def run_checked(
    argv: Sequence[str | os.PathLike[str]],
    *,
    env: Mapping[str, str],
    cwd: Path,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    """Run a verified executable without a shell and normalize failures."""
    normalized = _validated_argv(argv)
    working_directory = _validated_cwd(cwd)
    try:
        process = subprocess.Popen(
            normalized,
            cwd=working_directory,
            env=dict(env),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
            shell=False,
        )
    except OSError as error:
        raise ProviderError(
            "process_start_failed", "Provider process could not be started."
        ) from error

    failure_code: str | None = None
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _terminate_process(process)
        failure_code = "process_timeout"
    except UnicodeError:
        _terminate_process(process)
        failure_code = "process_output_invalid"

    if failure_code == "process_timeout":
        raise ProviderError("process_timeout", "Provider process timed out.")
    if failure_code == "process_output_invalid":
        raise ProviderError(
            "process_output_invalid", "Provider process output was invalid."
        )

    completed = subprocess.CompletedProcess(
        normalized,
        process.returncode,
        stdout,
        stderr,
    )
    if process.returncode != 0:
        raise ProviderError(
            "process_failed",
            f"Provider process failed with exit status {process.returncode}.",
        )
    return completed


def _validated_argv(
    argv: Sequence[str | os.PathLike[str]],
) -> list[str]:
    if not argv:
        raise ProviderError("invalid_executable", "Provider executable is invalid.")
    normalized = [os.fspath(value) for value in argv]
    executable = Path(normalized[0])
    try:
        mode = executable.stat().st_mode
    except OSError as error:
        raise ProviderError(
            "invalid_executable", "Provider executable is invalid."
        ) from error
    if (
        not executable.is_absolute()
        or not stat.S_ISREG(mode)
        or not os.access(executable, os.X_OK)
    ):
        raise ProviderError("invalid_executable", "Provider executable is invalid.")
    normalized[0] = str(executable.resolve())
    return normalized


def _validated_cwd(cwd: Path) -> Path:
    try:
        resolved = cwd.resolve(strict=True)
    except OSError as error:
        raise ProviderError(
            "invalid_working_directory",
            "Provider working directory is unavailable.",
        ) from error
    if not resolved.is_dir():
        raise ProviderError(
            "invalid_working_directory",
            "Provider working directory is unavailable.",
        )
    return resolved


def _terminate_process(process: subprocess.Popen[object]) -> None:
    process_group = process.pid
    if process_group == os.getpgrp():
        raise RuntimeError("refusing to signal the current process group")
    _signal_process_group(process_group, signal.SIGTERM)
    deadline = time.monotonic() + 2.0
    while _process_group_exists(process_group) and time.monotonic() < deadline:
        if process.poll() is None:
            try:
                process.wait(timeout=0.01)
            except subprocess.TimeoutExpired:
                pass
        else:
            time.sleep(0.01)
    if _process_group_exists(process_group):
        _signal_process_group(process_group, signal.SIGKILL)
    try:
        process.wait(timeout=0.5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _signal_process_group(process_group: int, requested_signal: int) -> None:
    try:
        os.killpg(process_group, requested_signal)
    except ProcessLookupError:
        pass


def _process_group_exists(process_group: int) -> bool:
    try:
        os.killpg(process_group, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class JsonRpcProcess:
    """A bounded, line-delimited JSON-RPC child process."""

    def __init__(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        env: Mapping[str, str],
        cwd: Path,
        timeout: float,
        on_notification: Callable[[str, Any], None] | None = None,
    ) -> None:
        self._argv = _validated_argv(argv)
        self._env = dict(env)
        self._cwd = _validated_cwd(cwd)
        self._timeout = timeout
        self._on_notification = on_notification
        self._condition = threading.Condition()
        self._write_lock = threading.Lock()
        self._responses: dict[int, dict[str, Any]] = {}
        self._request_states: dict[int, str] = {}
        self._failure: str | None = None
        self._next_id = 1
        self._process: subprocess.Popen[str] | None = None
        self._reader: threading.Thread | None = None
        self._closed = False

    def __enter__(self) -> "JsonRpcProcess":
        if self._process is not None:
            raise ProviderError("rpc_state", "RPC process is already running.")
        try:
            process = subprocess.Popen(
                self._argv,
                cwd=self._cwd,
                env=self._env,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                bufsize=1,
                start_new_session=True,
                shell=False,
            )
        except OSError as error:
            raise ProviderError(
                "rpc_start_failed", "RPC process could not be started."
            ) from error
        if process.stdin is None:
            _terminate_process(process)
            raise ProviderError(
                "rpc_start_failed", "RPC process could not be started."
            )
        try:
            os.set_blocking(process.stdin.fileno(), False)
        except OSError:
            _terminate_process(process)
            raise ProviderError(
                "rpc_start_failed", "RPC process could not be started."
            )
        self._process = process
        self._reader = threading.Thread(
            target=self._read_messages,
            name="dotsync-json-rpc-reader",
            daemon=True,
        )
        self._reader.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def request(
        self,
        method: str,
        params: Any,
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        if not method:
            raise ProviderError("rpc_method", "RPC method is invalid.")
        deadline = time.monotonic() + (
            self._timeout if timeout is None else timeout
        )
        with self._condition:
            request_id = self._next_id
            self._next_id += 1
            self._request_states[request_id] = "queued"
        try:
            self._send(
                {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "method": method,
                    "params": params,
                },
                method,
                deadline,
                cancel_event,
                request_id,
            )
        except BaseException:
            self._discard_request(request_id)
            raise

        while True:
            failure: str | None = None
            with self._condition:
                response = self._responses.pop(request_id, None)
                if response is not None:
                    return self._result_from_response(method, response)
                failure = self._failure
                if failure is None:
                    process = self._require_process()
                    if process.poll() is not None:
                        failure = "rpc_exited"
                if failure is None and cancel_event is not None:
                    if cancel_event.is_set():
                        failure = "rpc_cancelled"
                remaining = deadline - time.monotonic()
                if failure is None and remaining <= 0:
                    failure = "rpc_timeout"
                if failure is None:
                    interval = min(remaining, 0.05)
                    self._condition.wait(timeout=interval)
                    continue

            if failure in {"rpc_timeout", "rpc_cancelled"}:
                self._stop_process()
            self._discard_request(request_id)
            summary = {
                "rpc_cancelled": "was cancelled",
                "rpc_exited": "failed",
                "rpc_line_too_large": "received an oversized response",
                "rpc_protocol_error": "received a malformed response",
                "rpc_timeout": "timed out",
            }.get(failure, "failed")
            raise self._request_error(failure or "rpc_failed", method, summary)

    def notify(
        self,
        method: str,
        params: Any,
        *,
        cancel_event: threading.Event | None = None,
    ) -> None:
        if not method:
            raise ProviderError("rpc_method", "RPC method is invalid.")
        self._send(
            {"jsonrpc": "2.0", "method": method, "params": params},
            method,
            time.monotonic() + self._timeout,
            cancel_event,
            None,
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        process = self._process
        if process is None:
            return
        if process.stdin is not None:
            try:
                process.stdin.close()
            except OSError:
                pass
        self._stop_process()
        if process.stdout is not None:
            try:
                process.stdout.close()
            except OSError:
                pass
        reader = self._reader
        if reader is not None and reader is not threading.current_thread():
            reader.join(timeout=2.0)

    def _send(
        self,
        message: dict[str, Any],
        method: str,
        deadline: float,
        cancel_event: threading.Event | None,
        request_id: int | None,
    ) -> None:
        process = self._require_process()
        try:
            encoded = (
                json.dumps(message, separators=(",", ":")) + "\n"
            ).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise self._request_error(
                "rpc_request_invalid", method, "could not be encoded"
            ) from error
        with self._write_lock:
            if request_id is not None:
                with self._condition:
                    self._request_states[request_id] = "transmitting"
                    self._condition.notify_all()
            if process.stdin is None or process.poll() is not None:
                raise self._request_error("rpc_exited", method, "failed")
            selector: selectors.BaseSelector | None = None
            try:
                selector = selectors.DefaultSelector()
            except Exception:
                pass
            if selector is None:
                raise self._request_error(
                    "rpc_send_failed", method, "could not be sent"
                )
            send_failure: str | None = None
            send_summary = "could not be sent"
            active_exception: BaseException | None = None
            try:
                try:
                    selector.register(
                        process.stdin.fileno(), selectors.EVENT_WRITE
                    )
                    offset = 0
                    while offset < len(encoded):
                        failure = self._send_failure(deadline, cancel_event)
                        if failure is not None:
                            self._stop_process()
                            send_failure = failure
                            send_summary = (
                                "was cancelled"
                                if failure == "rpc_cancelled"
                                else "timed out"
                            )
                            break
                        if process.poll() is not None:
                            send_failure = "rpc_exited"
                            send_summary = "failed"
                            break
                        remaining = deadline - time.monotonic()
                        try:
                            stdin_fd = process.stdin.fileno()
                            events = selector.select(min(remaining, 0.05))
                        except Exception:
                            send_failure = "rpc_send_failed"
                            break
                        if not events:
                            continue
                        try:
                            offset += os.write(stdin_fd, encoded[offset:])
                        except BlockingIOError:
                            continue
                        except (BrokenPipeError, OSError, ValueError):
                            send_failure = "rpc_send_failed"
                            break
                except Exception:
                    send_failure = "rpc_send_failed"
                except BaseException as error:
                    active_exception = error
            finally:
                try:
                    selector.close()
                except Exception:
                    if active_exception is None and send_failure is None:
                        send_failure = "rpc_send_failed"
                except BaseException as error:
                    if active_exception is None and send_failure is None:
                        active_exception = error
            if active_exception is not None:
                raise active_exception
            if send_failure is not None:
                raise self._request_error(
                    send_failure,
                    method,
                    send_summary,
                )
            if request_id is not None:
                with self._condition:
                    self._request_states[request_id] = "transmitted"
                    self._condition.notify_all()

    def _send_failure(
        self,
        deadline: float,
        cancel_event: threading.Event | None,
    ) -> str | None:
        if cancel_event is not None and cancel_event.is_set():
            return "rpc_cancelled"
        if time.monotonic() >= deadline:
            return "rpc_timeout"
        return None

    def _read_messages(self) -> None:
        process = self._require_process()
        stream = process.stdout
        if stream is None:
            self._set_failure("rpc_protocol_error")
            return
        while True:
            try:
                line = stream.readline(_MAX_RPC_LINE_BYTES + 1)
            except (OSError, UnicodeError, ValueError):
                if not self._closed:
                    self._set_failure("rpc_protocol_error")
                return
            if not line:
                if not self._closed:
                    try:
                        process.wait(timeout=0.1)
                    except subprocess.TimeoutExpired:
                        pass
                    self._set_failure("rpc_exited")
                return
            if len(line.encode("utf-8")) > _MAX_RPC_LINE_BYTES:
                self._set_failure("rpc_line_too_large")
                return
            try:
                message = json.loads(line)
            except (json.JSONDecodeError, UnicodeError):
                self._set_failure("rpc_protocol_error")
                return
            if not isinstance(message, dict):
                self._set_failure("rpc_protocol_error")
                return
            if "id" not in message:
                if not self._handle_notification(message):
                    self._set_failure("rpc_protocol_error")
                    return
                continue
            response_id = message["id"]
            if type(response_id) is not int:
                self._set_failure("rpc_protocol_error")
                return
            with self._condition:
                while (
                    self._request_states.get(response_id) == "transmitting"
                    and self._failure is None
                ):
                    self._condition.wait()
                if self._request_states.get(response_id) != "transmitted":
                    if self._failure is None:
                        self._failure = "rpc_protocol_error"
                    self._condition.notify_all()
                    return
                self._request_states.pop(response_id, None)
                self._responses[response_id] = message
                self._condition.notify_all()

    def _handle_notification(self, message: dict[str, Any]) -> bool:
        method = message.get("method")
        if not isinstance(method, str):
            return False
        callback = self._on_notification
        if callback is not None:
            try:
                callback(method, message.get("params"))
            except Exception:
                return False
        return True

    def _result_from_response(
        self, method: str, response: dict[str, Any]
    ) -> Any:
        if "error" in response:
            remote_error = response["error"]
            if (
                "result" in response
                or type(remote_error) is not dict
                or type(remote_error.get("code")) is not int
                or type(remote_error.get("message")) is not str
            ):
                raise self._request_error(
                    "rpc_protocol_error",
                    method,
                    "received a malformed response",
                )
            remote_code = remote_error["code"]
            if remote_code == -32600:
                safe_code = (
                    "rpc_authentication_error"
                    if method == "account/rateLimits/read"
                    else "rpc_invalid_request"
                )
            else:
                safe_code = _SAFE_REMOTE_ERROR_CODES.get(
                    remote_code,
                    "rpc_remote_error",
                )
            raise self._request_error(
                safe_code, method, "returned an error"
            )
        if "result" not in response:
            raise self._request_error(
                "rpc_protocol_error", method, "received a malformed response"
            )
        return response["result"]

    def _set_failure(self, code: str) -> None:
        with self._condition:
            if self._failure is None:
                self._failure = code
            self._condition.notify_all()

    def _discard_request(self, request_id: int) -> None:
        with self._condition:
            self._request_states.pop(request_id, None)
            self._responses.pop(request_id, None)
            self._condition.notify_all()

    def _request_error(
        self, code: str, method: str, summary: str
    ) -> ProviderError:
        process = self._process
        status = None if process is None else process.poll()
        status_text = "unavailable" if status is None else str(status)
        return ProviderError(
            code,
            f"RPC method {method!r} {summary} (exit status {status_text}).",
        )

    def _require_process(self) -> subprocess.Popen[str]:
        if self._process is None:
            raise ProviderError("rpc_state", "RPC process is not running.")
        return self._process

    def _stop_process(self) -> None:
        process = self._process
        if process is not None:
            _terminate_process(process)


class PtySession:
    """A bounded pseudo-terminal session for interactive provider login."""

    def __init__(
        self,
        argv: Sequence[str | os.PathLike[str]],
        *,
        env: Mapping[str, str],
        cwd: Path,
    ) -> None:
        self._argv = _validated_argv(argv)
        self._env = dict(env)
        self._cwd = _validated_cwd(cwd)
        self._process: subprocess.Popen[bytes] | None = None
        self._master_fd: int | None = None
        self._selector: selectors.BaseSelector | None = None
        self._output = bytearray()
        self._terminated = False

    def __enter__(self) -> "PtySession":
        if self._process is not None:
            raise ProviderError("pty_state", "PTY process is already running.")
        master_fd: int | None = None
        slave_fd: int | None = None
        process: subprocess.Popen[bytes] | None = None
        selector: selectors.BaseSelector | None = None
        setup_failed = False
        try:
            master_fd, slave_fd = pty.openpty()
            process = subprocess.Popen(
                self._argv,
                cwd=self._cwd,
                env=self._env,
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
                shell=False,
            )
            os.close(slave_fd)
            slave_fd = None
            os.set_blocking(master_fd, False)
            selector = selectors.DefaultSelector()
            selector.register(master_fd, selectors.EVENT_READ)
        except Exception:
            setup_failed = True
        if setup_failed:
            if selector is not None:
                try:
                    selector.close()
                except Exception:
                    pass
            if process is not None:
                _terminate_process(process)
            for file_descriptor in (master_fd, slave_fd):
                if file_descriptor is not None:
                    try:
                        os.close(file_descriptor)
                    except OSError:
                        pass
            raise ProviderError(
                "pty_start_failed", "PTY process could not be started."
            )
        if master_fd is None or process is None or selector is None:
            raise ProviderError(
                "pty_start_failed", "PTY process could not be started."
            )
        self._process = process
        self._master_fd = master_fd
        self._selector = selector
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.terminate()

    def write_line(self, value: str) -> None:
        if "\n" in value or "\r" in value:
            raise ProviderError("pty_input_invalid", "PTY input must be one line.")
        encoded = (value + "\n").encode("utf-8")
        if len(encoded) > _MAX_PTY_INPUT_BYTES:
            raise ProviderError("pty_input_invalid", "PTY input is too large.")
        master_fd = self._require_master_fd()
        offset = 0
        while offset < len(encoded):
            try:
                offset += os.write(master_fd, encoded[offset:])
            except BlockingIOError:
                time.sleep(0.01)
            except OSError as error:
                raise self._pty_error(
                    "pty_write_failed", "could not accept input"
                ) from error

    def read_until(
        self,
        predicate: Callable[[str], bool],
        timeout: float,
        cancel_event: threading.Event | None = None,
    ) -> str:
        selector = self._require_selector()
        failure: tuple[str, str] | None = None
        chunk = b""
        current, invalid_output = self._decoded_output()
        if invalid_output:
            failure = ("pty_output_invalid", "produced invalid output")
        elif current is not None and predicate(current):
            return current
        deadline = time.monotonic() + timeout
        while failure is None:
            if cancel_event is not None and cancel_event.is_set():
                failure = ("pty_cancelled", "was cancelled")
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = ("pty_timeout", "timed out")
                break
            interval = (
                min(remaining, 0.05)
                if cancel_event is not None
                else remaining
            )
            events = selector.select(interval)
            if not events:
                process = self._require_process()
                if process.poll() is not None:
                    current = None
                    failure = self._exit_failure()
                continue
            reached_eof = False
            chunk = b""
            try:
                chunk = os.read(self._require_master_fd(), 65536)
            except BlockingIOError:
                continue
            except OSError as error:
                if error.errno != errno.EIO:
                    failure = ("pty_read_failed", "could not be read")
                else:
                    reached_eof = True
            if failure is not None:
                break
            if reached_eof or not chunk:
                chunk = b""
                current = None
                self._wait_for_exit()
                failure = self._exit_failure()
                break
            if len(self._output) + len(chunk) > _MAX_PTY_OUTPUT_BYTES:
                failure = (
                    "pty_output_limit",
                    "exceeded its output limit",
                )
                break
            self._output.extend(chunk)
            chunk = b""
            current, invalid_output = self._decoded_output()
            if invalid_output:
                failure = (
                    "pty_output_invalid",
                    "produced invalid output",
                )
                break
            if current is not None and predicate(current):
                return current

        failure_code, failure_summary = failure
        failure = None
        chunk = b""
        current = None
        self._output.clear()
        self.terminate()
        raise self._pty_error(failure_code, failure_summary)

    def terminate(self) -> None:
        if self._terminated:
            return
        self._terminated = True
        selector = self._selector
        self._selector = None
        if selector is not None:
            selector.close()
        process = self._process
        if process is not None:
            _terminate_process(process)
        master_fd = self._master_fd
        self._master_fd = None
        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass

    def _decoded_output(
        self, *, final: bool = False
    ) -> tuple[str | None, bool]:
        raw_output = bytes(self._output)
        decoder = codecs.getincrementaldecoder("utf-8")(errors="strict")
        decoded: str | None = None
        pending = False
        try:
            decoded = decoder.decode(raw_output, final=final)
            pending = bool(decoder.getstate()[0])
        except UnicodeError:
            self._output.clear()
        raw_output = b""
        decoder = None
        if decoded is None:
            return None, True
        if pending:
            return None, False
        return decoded, False

    def _exit_failure(self) -> tuple[str, str]:
        result = self._decoded_output(final=True)
        if result[1]:
            return "pty_output_invalid", "produced invalid output"
        return "pty_exited", "exited"

    def _wait_for_exit(self) -> None:
        process = self._require_process()
        try:
            process.wait(timeout=0.1)
        except subprocess.TimeoutExpired:
            pass

    def _pty_error(self, code: str, summary: str) -> ProviderError:
        process = self._process
        status = None if process is None else process.poll()
        status_text = "unavailable" if status is None else str(status)
        return ProviderError(
            code,
            f"PTY process {summary} (exit status {status_text}).",
        )

    def _require_process(self) -> subprocess.Popen[bytes]:
        if self._process is None:
            raise ProviderError("pty_state", "PTY process is not running.")
        return self._process

    def _require_master_fd(self) -> int:
        if self._master_fd is None:
            raise ProviderError("pty_state", "PTY process is not running.")
        return self._master_fd

    def _require_selector(self) -> selectors.BaseSelector:
        if self._selector is None:
            raise ProviderError("pty_state", "PTY process is not running.")
        return self._selector

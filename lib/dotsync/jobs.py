"""Bounded asynchronous jobs for composition-root registered operations."""

from __future__ import annotations

import copy
import json
import math
import re
import threading
import time
import unicodedata
import uuid
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, cast

from dotsync.providers import ProviderError


JobState = Literal[
    "queued",
    "running",
    "waiting_for_user",
    "succeeded",
    "failed",
]
JobOperation = Callable[["JobContext"], dict[str, object] | None]


class JobNotFound(LookupError):
    """Raised when a job does not exist or has passed its retention window."""


class UnknownJobKind(ValueError):
    """Raised when a caller requests an unregistered operation."""


class RegistryClosed(RuntimeError):
    """Raised when work is submitted after shutdown begins."""


class _JobFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class _ChildProcess(Protocol):
    def terminate(self) -> object:
        raise NotImplementedError

    def wait(self, timeout: float | None = None) -> object:
        raise NotImplementedError

    def kill(self) -> object:
        raise NotImplementedError


@dataclass(frozen=True)
class Job:
    id: str
    kind: str
    account_id: str | None


@dataclass(frozen=True)
class JobView:
    id: str
    kind: str
    state: JobState
    account_id: str | None
    progress: dict[str, str]
    result: dict[str, object] | None
    error_code: str | None


@dataclass
class _JobRecord:
    job: Job
    cancel_event: threading.Event
    state: JobState = "queued"
    progress: dict[str, str] = field(default_factory=dict)
    result: dict[str, object] | None = None
    error_code: str | None = None
    finished_at: float | None = None
    future: Future[None] | None = None
    children: list[_ChildProcess] = field(default_factory=list)


class JobContext:
    """Cancellation, progress, and child ownership passed to one job."""

    def __init__(
        self,
        *,
        account_id: str | None,
        cancel_event: threading.Event,
        update_progress: Callable[[dict[str, str], bool], None],
        register_child: Callable[[_ChildProcess], None],
        unregister_child: Callable[[_ChildProcess], None],
    ) -> None:
        self.account_id = account_id
        self.cancel_event = cancel_event
        self._update_progress = update_progress
        self._register_child = register_child
        self._unregister_child = unregister_child

    def report(self, progress: dict[str, str]) -> None:
        self._update_progress(progress, False)

    def waiting_for_user(self, progress: dict[str, str]) -> None:
        self._update_progress(progress, True)

    def register_child(self, process: _ChildProcess) -> None:
        self._register_child(process)

    def unregister_child(self, process: _ChildProcess) -> None:
        self._unregister_child(process)


_MAX_WORKERS = 4
_MAX_SAFE_JSON_BYTES = 64 * 1024
_TERMINAL_RETENTION_SECONDS = 30 * 60
_SHUTDOWN_GRACE_SECONDS = 2.0
_ERROR_CODE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_PROVIDER_CODES = frozenset(
    {
        "cli_missing",
        "login_cancelled",
        "logout_cancelled",
        "logout_failed",
        "provider_unavailable",
        "reauth_required",
        "refresh_cancelled",
        "refresh_timeout",
        "unsafe_account_path",
        "unsupported_cli_version",
        "unsupported_usage_layout",
    }
)
_TERMINAL_STATES = frozenset({"succeeded", "failed"})
_DANGEROUS_BIDI_CLASSES = frozenset(
    {"LRE", "RLE", "LRO", "RLO", "PDF", "LRI", "RLI", "FSI", "PDI"}
)


class JobRegistry:
    """Run a fixed operation catalog with bounded workers and safe views."""

    def __init__(
        self,
        operations: Mapping[str, JobOperation],
        *,
        max_workers: int = _MAX_WORKERS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        if type(max_workers) is not int or not 1 <= max_workers <= _MAX_WORKERS:
            raise ValueError("job worker count must be between one and four")
        self._operations = _validate_operations(operations)
        self._monotonic = monotonic
        self._condition = threading.Condition(threading.RLock())
        self._jobs: dict[str, _JobRecord] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="dotsync-job",
        )
        self._shutting_down = False
        self._shutdown_complete = False

    def __enter__(self) -> "JobRegistry":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.shutdown()

    def submit(self, kind: str, *, account_id: str | None = None) -> Job:
        validated_kind = _validate_text(kind, "job kind")
        validated_account_id = (
            None
            if account_id is None
            else _validate_text(account_id, "job account id")
        )
        with self._condition:
            self._prune_locked()
            if self._shutting_down:
                raise RegistryClosed("job registry is shutting down")
            if validated_kind not in self._operations:
                raise UnknownJobKind("job kind is not registered")
            job = Job(
                id=str(uuid.uuid4()),
                kind=validated_kind,
                account_id=validated_account_id,
            )
            record = _JobRecord(job=job, cancel_event=threading.Event())
            self._jobs[job.id] = record
            try:
                record.future = self._executor.submit(self._execute, job.id)
            except RuntimeError:
                del self._jobs[job.id]
                raise RegistryClosed("job registry is shutting down") from None
            self._condition.notify_all()
            return job

    def get(self, job_id: str) -> JobView:
        with self._condition:
            self._prune_locked()
            return self._view_locked(self._record_locked(job_id))

    def list_jobs(self) -> list[JobView]:
        with self._condition:
            self._prune_locked()
            return [
                self._view_locked(record)
                for record in sorted(
                    self._jobs.values(),
                    key=lambda record: record.job.id,
                )
            ]

    def wait(self, job_id: str, *, timeout: float | None = None) -> JobView:
        if timeout is not None and (
            type(timeout) not in {int, float}
            or isinstance(timeout, bool)
            or not math.isfinite(float(timeout))
            or timeout < 0
        ):
            raise ValueError("job wait timeout must be a non-negative number")
        deadline = None if timeout is None else time.monotonic() + float(timeout)
        with self._condition:
            while True:
                self._prune_locked()
                record = self._record_locked(job_id)
                if record.state in _TERMINAL_STATES:
                    return self._view_locked(record)
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError("job did not finish in time")
                self._condition.wait(timeout=remaining)

    def cancel(self, job_id: str) -> JobView:
        with self._condition:
            self._prune_locked()
            record = self._record_locked(job_id)
            if record.state in _TERMINAL_STATES:
                return self._view_locked(record)
            record.cancel_event.set()
            if (
                record.state == "queued"
                and record.future is not None
                and record.future.cancel()
            ):
                self._finish_locked(record, error_code="cancelled")
            self._condition.notify_all()
            return self._view_locked(record)

    def shutdown(self) -> None:
        with self._condition:
            if self._shutdown_complete:
                return
            if self._shutting_down:
                self._condition.wait_for(lambda: self._shutdown_complete)
                return
            self._shutting_down = True
            for record in self._jobs.values():
                if record.state in _TERMINAL_STATES:
                    continue
                record.cancel_event.set()
                if (
                    record.state == "queued"
                    and record.future is not None
                    and record.future.cancel()
                ):
                    self._finish_locked(record, error_code="cancelled")
            self._condition.notify_all()

            deadline = time.monotonic() + _SHUTDOWN_GRACE_SECONDS
            while self._has_active_jobs_locked():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                self._condition.wait(timeout=remaining)
            children = self._registered_children_locked()

        for child in children:
            _terminate_child(child)

        self._executor.shutdown(wait=True, cancel_futures=True)
        with self._condition:
            for record in self._jobs.values():
                if record.state not in _TERMINAL_STATES:
                    self._finish_locked(record, error_code="cancelled")
            self._shutdown_complete = True
            self._condition.notify_all()

    def _execute(self, job_id: str) -> None:
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None or record.state != "queued":
                return
            if record.cancel_event.is_set():
                self._finish_locked(record, error_code="cancelled")
                return
            record.state = "running"
            operation = self._operations[record.job.kind]
            context = JobContext(
                account_id=record.job.account_id,
                cancel_event=record.cancel_event,
                update_progress=lambda progress, waiting: self._update_progress(
                    job_id,
                    progress,
                    waiting,
                ),
                register_child=lambda child: self._register_child(job_id, child),
                unregister_child=lambda child: self._unregister_child(job_id, child),
            )
            self._condition.notify_all()

        result: dict[str, object] | None = None
        error_code: str | None = None
        try:
            result = _safe_result(operation(context))
        except _JobFailure as error:
            error_code = error.code
        except ProviderError as error:
            error_code = _safe_provider_code(error.code)
        except BaseException:
            error_code = "job_failed"

        children = self._take_children(job_id)
        for child in children:
            _terminate_child(child)

        with self._condition:
            record = self._jobs.get(job_id)
            if record is None or record.state in _TERMINAL_STATES:
                return
            if record.cancel_event.is_set():
                self._finish_locked(record, error_code="cancelled")
            elif error_code is not None:
                self._finish_locked(record, error_code=error_code)
            else:
                self._finish_locked(record, result=result)

    def _update_progress(
        self,
        job_id: str,
        progress: dict[str, str],
        waiting_for_user: bool,
    ) -> None:
        safe_progress = _safe_progress(progress)
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None or record.state in _TERMINAL_STATES:
                raise _JobFailure("invalid_job_progress")
            record.progress = safe_progress
            record.state = "waiting_for_user" if waiting_for_user else "running"
            self._condition.notify_all()

    def _register_child(self, job_id: str, child: _ChildProcess) -> None:
        if not all(
            callable(getattr(child, method, None))
            for method in ("terminate", "wait", "kill")
        ):
            raise _JobFailure("invalid_job_child")
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None or record.state in _TERMINAL_STATES:
                raise _JobFailure("invalid_job_child")
            if all(existing is not child for existing in record.children):
                record.children.append(child)
            self._condition.notify_all()

    def _unregister_child(self, job_id: str, child: _ChildProcess) -> None:
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None:
                return
            record.children = [
                existing for existing in record.children if existing is not child
            ]
            self._condition.notify_all()

    def _take_children(self, job_id: str) -> list[_ChildProcess]:
        with self._condition:
            record = self._jobs.get(job_id)
            if record is None:
                return []
            children = record.children
            record.children = []
            return children

    def _finish_locked(
        self,
        record: _JobRecord,
        *,
        result: dict[str, object] | None = None,
        error_code: str | None = None,
    ) -> None:
        record.state = "failed" if error_code is not None else "succeeded"
        record.result = result if error_code is None else None
        record.error_code = error_code
        record.finished_at = self._monotonic()
        self._condition.notify_all()

    def _record_locked(self, job_id: str) -> _JobRecord:
        if type(job_id) is not str:
            raise JobNotFound("job not found")
        record = self._jobs.get(job_id)
        if record is None:
            raise JobNotFound("job not found")
        return record

    @staticmethod
    def _view_locked(record: _JobRecord) -> JobView:
        return JobView(
            id=record.job.id,
            kind=record.job.kind,
            state=record.state,
            account_id=record.job.account_id,
            progress=dict(record.progress),
            result=copy.deepcopy(record.result),
            error_code=record.error_code,
        )

    def _prune_locked(self) -> None:
        now = self._monotonic()
        expired = [
            job_id
            for job_id, record in self._jobs.items()
            if record.finished_at is not None
            and now - record.finished_at >= _TERMINAL_RETENTION_SECONDS
        ]
        for job_id in expired:
            del self._jobs[job_id]

    def _has_active_jobs_locked(self) -> bool:
        return any(
            record.state not in _TERMINAL_STATES for record in self._jobs.values()
        )

    def _registered_children_locked(self) -> list[_ChildProcess]:
        children: list[_ChildProcess] = []
        for record in self._jobs.values():
            for child in record.children:
                if all(existing is not child for existing in children):
                    children.append(child)
        return children


def _validate_operations(
    operations: Mapping[str, JobOperation],
) -> dict[str, JobOperation]:
    if not isinstance(operations, Mapping) or not operations:
        raise ValueError("job operations must be a non-empty mapping")
    validated: dict[str, JobOperation] = {}
    for kind, operation in operations.items():
        validated_kind = _validate_text(kind, "job kind")
        if not callable(operation):
            raise TypeError("registered job operation must be callable")
        validated[validated_kind] = operation
    return validated


def _safe_progress(progress: dict[str, str]) -> dict[str, str]:
    if type(progress) is not dict or any(
        type(key) is not str or type(value) is not str
        for key, value in progress.items()
    ):
        raise _JobFailure("invalid_job_progress")
    try:
        safe = _safe_json_object(cast(dict[str, object], progress))
    except _JobFailure:
        raise _JobFailure("invalid_job_progress") from None
    return cast(dict[str, str], safe)


def _safe_result(value: object) -> dict[str, object] | None:
    if value is None:
        return None
    if type(value) is not dict:
        raise _JobFailure("invalid_job_result")
    try:
        return _safe_json_object(cast(dict[str, object], value))
    except _JobFailure:
        raise _JobFailure("invalid_job_result") from None


def _safe_json_object(value: dict[str, object]) -> dict[str, object]:
    try:
        _validate_json_value(value)
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except (OverflowError, RecursionError, TypeError, ValueError):
        raise _JobFailure("invalid_job_json") from None
    if len(encoded) > _MAX_SAFE_JSON_BYTES:
        raise _JobFailure("invalid_job_json")
    decoded = json.loads(encoded.decode("utf-8"))
    return cast(dict[str, object], decoded)


def _validate_json_value(value: object) -> None:
    if value is None or type(value) in {bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(cast(float, value)):
            raise ValueError("non-finite job JSON number")
        return
    if type(value) is str:
        _validate_text(value, "job JSON string", allow_empty=True)
        return
    if type(value) is list:
        for item in cast(list[object], value):
            _validate_json_value(item)
        return
    if type(value) is dict:
        for key, item in cast(dict[object, object], value).items():
            if type(key) is not str:
                raise TypeError("job JSON object keys must be strings")
            _validate_text(key, "job JSON key", allow_empty=True)
            _validate_json_value(item)
        return
    raise TypeError("unsupported job JSON value")


def _validate_text(value: object, field: str, *, allow_empty: bool = False) -> str:
    if type(value) is not str or (not allow_empty and not value):
        raise ValueError(f"{field} must be a safe string")
    for character in value:
        if (
            unicodedata.category(character) in {"Cc", "Cs"}
            or unicodedata.bidirectional(character) in _DANGEROUS_BIDI_CLASSES
        ):
            raise ValueError(f"{field} must be a safe string")
    return value


def _safe_provider_code(code: object) -> str:
    if (
        type(code) is str
        and _ERROR_CODE.fullmatch(code) is not None
        and code in _SAFE_PROVIDER_CODES
    ):
        return code
    return "job_failed"


def _terminate_child(child: _ChildProcess) -> None:
    terminated = False
    try:
        child.terminate()
        terminated = True
    except BaseException:
        pass
    if terminated:
        try:
            child.wait(timeout=1.0)
            return
        except BaseException:
            pass
    try:
        child.kill()
    except BaseException:
        return
    try:
        child.wait(timeout=1.0)
    except BaseException:
        pass

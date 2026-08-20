from __future__ import annotations

import threading

import pytest

from dotsync.providers import ProviderError


def test_registry_runs_only_composition_registered_kinds():
    from dotsync.jobs import JobRegistry, UnknownJobKind

    registry = JobRegistry({"refresh": lambda context: {"status": "ok"}})
    try:
        with pytest.raises(UnknownJobKind, match="registered"):
            registry.submit("arbitrary")

        job = registry.submit("refresh", account_id="account-1")
        view = registry.wait(job.id, timeout=2)

        assert view.kind == "refresh"
        assert view.account_id == "account-1"
        assert view.state == "succeeded"
        assert view.progress == {}
        assert view.result == {"status": "ok"}
        assert view.error_code is None
    finally:
        registry.shutdown()


def test_registry_rejects_worker_count_above_four():
    from dotsync.jobs import JobRegistry

    with pytest.raises(ValueError, match="four"):
        JobRegistry({"noop": lambda context: None}, max_workers=5)


def test_registry_runs_at_most_four_callables_at_once():
    from dotsync.jobs import JobRegistry

    condition = threading.Condition()
    release = threading.Event()
    entered = 0
    active = 0
    maximum_active = 0

    def blocking_operation(context):
        nonlocal entered, active, maximum_active
        with condition:
            entered += 1
            active += 1
            maximum_active = max(maximum_active, active)
            condition.notify_all()
        try:
            assert release.wait(timeout=2)
            return {"status": "ok"}
        finally:
            with condition:
                active -= 1
                condition.notify_all()

    registry = JobRegistry({"refresh": blocking_operation})
    jobs = [registry.submit("refresh") for _ in range(5)]
    try:
        with condition:
            assert condition.wait_for(lambda: entered == 4, timeout=2)
        assert maximum_active == 4

        release.set()
        views = [registry.wait(job.id, timeout=2) for job in jobs]

        assert all(view.state == "succeeded" for view in views)
        assert entered == 5
        assert maximum_active == 4
    finally:
        release.set()
        registry.shutdown()


def test_cancelling_queued_job_prevents_invocation():
    from dotsync.jobs import JobRegistry

    entered = threading.Event()
    release = threading.Event()
    queued_invoked = threading.Event()

    def blocking_operation(context):
        entered.set()
        assert release.wait(timeout=2)
        return None

    def queued_operation(context):
        queued_invoked.set()
        return None

    registry = JobRegistry(
        {"blocking": blocking_operation, "queued": queued_operation},
        max_workers=1,
    )
    blocking = registry.submit("blocking")
    assert entered.wait(timeout=2)
    queued = registry.submit("queued")
    try:
        cancelled = registry.cancel(queued.id)
        release.set()
        registry.wait(blocking.id, timeout=2)

        assert cancelled.state == "failed"
        assert cancelled.error_code == "cancelled"
        assert queued_invoked.is_set() is False
    finally:
        release.set()
        registry.shutdown()


def test_running_cancellation_uses_event_and_failed_state():
    from dotsync.jobs import JobRegistry

    entered = threading.Event()
    observed_cancel = threading.Event()

    def operation(context):
        entered.set()
        assert context.cancel_event.wait(timeout=2)
        observed_cancel.set()
        return {"ignored": "result"}

    registry = JobRegistry({"login": operation})
    job = registry.submit("login")
    try:
        assert entered.wait(timeout=2)
        registry.cancel(job.id)
        view = registry.wait(job.id, timeout=2)

        assert observed_cancel.is_set()
        assert view.state == "failed"
        assert view.result is None
        assert view.error_code == "cancelled"
    finally:
        registry.shutdown()


def test_waiting_for_user_and_progress_are_safe_observable_states():
    from dotsync.jobs import JobRegistry

    waiting = threading.Event()
    release = threading.Event()

    def login(context):
        context.waiting_for_user({"step": "open_browser"})
        waiting.set()
        assert release.wait(timeout=2)
        context.report({"step": "authenticated"})
        return {"status": "ready"}

    registry = JobRegistry({"login": login})
    job = registry.submit("login", account_id="account-1")
    try:
        assert waiting.wait(timeout=2)
        waiting_view = registry.get(job.id)
        assert waiting_view.state == "waiting_for_user"
        assert waiting_view.progress == {"step": "open_browser"}

        release.set()
        completed = registry.wait(job.id, timeout=2)
        assert completed.state == "succeeded"
        assert completed.progress == {"step": "authenticated"}
        assert completed.result == {"status": "ready"}
    finally:
        release.set()
        registry.shutdown()


@pytest.mark.parametrize(
    ("operation", "expected_code"),
    [
        (
            lambda context: context.report({"payload": "x" * (64 * 1024)}),
            "invalid_job_progress",
        ),
        (lambda context: {"payload": "x" * (64 * 1024)}, "invalid_job_result"),
        (lambda context: ["not", "an", "object"], "invalid_job_result"),
    ],
)
def test_progress_and_result_json_are_bounded_and_exact(operation, expected_code):
    from dotsync.jobs import JobRegistry

    registry = JobRegistry({"unsafe": operation})
    try:
        job = registry.submit("unsafe")
        view = registry.wait(job.id, timeout=2)

        assert view.state == "failed"
        assert view.progress == {}
        assert view.result is None
        assert view.error_code == expected_code
    finally:
        registry.shutdown()


def test_result_view_is_isolated_from_callable_and_caller_mutation():
    from dotsync.jobs import JobRegistry

    result = {"nested": {"value": "original"}}
    registry = JobRegistry({"read": lambda context: result})
    try:
        job = registry.submit("read")
        first = registry.wait(job.id, timeout=2)
        result["nested"]["value"] = "callable-mutated"
        first.result["nested"]["value"] = "caller-mutated"

        second = registry.get(job.id)

        assert second.result == {"nested": {"value": "original"}}
    finally:
        registry.shutdown()


def test_exception_with_sentinel_stores_only_generic_error_code():
    from dotsync.jobs import JobRegistry

    sentinel = "SENTINEL_SECRET_EXCEPTION_TOKEN"

    def fail(context):
        raise RuntimeError(sentinel)

    registry = JobRegistry({"fail": fail})
    try:
        job = registry.submit("fail")
        view = registry.wait(job.id, timeout=2)

        assert view.state == "failed"
        assert view.error_code == "job_failed"
        assert sentinel not in str(view)
        assert view.result is None
    finally:
        registry.shutdown()


@pytest.mark.parametrize(
    "normalized_code",
    ["provider_unavailable", "unsupported_usage_layout"],
)
def test_provider_exception_keeps_normalized_code_without_safe_message(
    normalized_code,
):
    from dotsync.jobs import JobRegistry

    sentinel = "SENTINEL_PROVIDER_MESSAGE_TOKEN"

    def fail(context):
        raise ProviderError(normalized_code, sentinel)

    registry = JobRegistry({"refresh": fail})
    try:
        job = registry.submit("refresh")
        view = registry.wait(job.id, timeout=2)

        assert view.state == "failed"
        assert view.error_code == normalized_code
        assert sentinel not in str(view)
    finally:
        registry.shutdown()


def test_terminal_jobs_expire_after_thirty_minutes_with_injected_clock():
    from dotsync.jobs import JobNotFound, JobRegistry

    current = [100.0]
    registry = JobRegistry(
        {"noop": lambda context: None},
        monotonic=lambda: current[0],
    )
    try:
        job = registry.submit("noop")
        assert registry.wait(job.id, timeout=2).state == "succeeded"

        current[0] += 1799.0
        assert registry.get(job.id).state == "succeeded"
        current[0] += 2.0

        with pytest.raises(JobNotFound, match="not found"):
            registry.get(job.id)
    finally:
        registry.shutdown()


def test_shutdown_waits_for_running_logout_and_rejects_new_jobs():
    from dotsync.jobs import JobRegistry, RegistryClosed

    entered = threading.Event()
    cancellation_seen = threading.Event()
    release = threading.Event()

    def logout(context):
        entered.set()
        assert context.cancel_event.wait(timeout=2)
        cancellation_seen.set()
        assert release.wait(timeout=2)
        return None

    registry = JobRegistry({"logout": logout})
    job = registry.submit("logout")
    assert entered.wait(timeout=2)
    shutdown_thread = threading.Thread(target=registry.shutdown)
    shutdown_thread.start()
    assert cancellation_seen.wait(timeout=2)

    with pytest.raises(RegistryClosed, match="shutting down"):
        registry.submit("logout")
    assert shutdown_thread.is_alive()

    release.set()
    shutdown_thread.join(timeout=2)
    assert not shutdown_thread.is_alive()
    assert registry.get(job.id).state == "failed"
    assert registry.get(job.id).error_code == "cancelled"


def test_shutdown_terminates_registered_child_before_joining_worker():
    from dotsync.jobs import JobRegistry

    class FakeChild:
        def __init__(self) -> None:
            self.terminated = threading.Event()

        def terminate(self):
            self.terminated.set()

        def wait(self, timeout=None):
            assert self.terminated.wait(timeout=timeout)
            return 0

        def kill(self):
            self.terminated.set()

    child = FakeChild()
    registered = threading.Event()

    def blocked_child(context):
        context.register_child(child)
        registered.set()
        assert child.terminated.wait(timeout=4)
        return None

    registry = JobRegistry({"child": blocked_child})
    registry.submit("child")
    assert registered.wait(timeout=2)

    registry.shutdown()

    assert child.terminated.is_set()


def test_child_registered_after_forced_teardown_is_immediately_terminated(
    monkeypatch,
):
    import dotsync.jobs as jobs_module
    from dotsync.jobs import JobRegistry

    monkeypatch.setattr(jobs_module, "_SHUTDOWN_GRACE_SECONDS", 0.0)

    class LateChild:
        def __init__(self) -> None:
            self.terminated = threading.Event()

        def terminate(self):
            self.terminated.set()

        def wait(self, timeout=None):
            assert self.terminated.wait(timeout=timeout)
            return 0

        def kill(self):
            self.terminated.set()

    child = LateChild()
    entered = threading.Event()
    register_late = threading.Event()
    registration_returned = threading.Event()

    def operation(context):
        entered.set()
        assert register_late.wait(timeout=2)
        try:
            context.register_child(child)
        finally:
            registration_returned.set()
        return None

    registry = JobRegistry({"late-child": operation})
    job = registry.submit("late-child")
    assert entered.wait(timeout=2)
    shutdown = threading.Thread(target=registry.shutdown)
    shutdown.start()
    assert registry._wait_for_forced_teardown(timeout=2)

    register_late.set()
    assert registration_returned.wait(timeout=2)
    assert child.terminated.wait(timeout=2)
    shutdown.join(timeout=2)

    assert not shutdown.is_alive()
    assert registry.get(job.id).state == "failed"
    assert registry.get(job.id).error_code == "cancelled"


def test_shutdown_is_bounded_when_callable_ignores_cancellation(monkeypatch):
    import dotsync.jobs as jobs_module
    from dotsync.jobs import JobRegistry

    monkeypatch.setattr(jobs_module, "_SHUTDOWN_GRACE_SECONDS", 0.0)
    entered = threading.Event()
    release_noncooperative_job = threading.Event()

    def noncooperative(context):
        entered.set()
        release_noncooperative_job.wait()
        return None

    registry = JobRegistry({"noncooperative": noncooperative}, max_workers=1)
    job = registry.submit("noncooperative")
    assert entered.wait(timeout=2)

    shutdown = threading.Thread(target=registry.shutdown)
    shutdown.start()
    shutdown.join(timeout=1)
    try:
        assert not shutdown.is_alive()
        view = registry.get(job.id)
        assert view.state == "failed"
        assert view.error_code == "cancelled"
    finally:
        release_noncooperative_job.set()
        shutdown.join(timeout=2)


def test_context_manager_shutdown_is_idempotent():
    from dotsync.jobs import JobRegistry

    with JobRegistry({"noop": lambda context: None}) as registry:
        job = registry.submit("noop")
        assert registry.wait(job.id, timeout=2).state == "succeeded"

    registry.shutdown()

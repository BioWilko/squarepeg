import os
import signal
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError, InterruptError, RunnerError
from squarepeg.k8s import runner
from squarepeg.runspec import RunSpec


def make_session():
    session = SimpleNamespace()
    session.core = MagicMock()
    session.batch = MagicMock()
    session.namespace = "default"
    return session


def pod_object(name="squarepeg-alpine-abc123", phase="Running", container_statuses=None):
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name),
        status=SimpleNamespace(phase=phase, container_statuses=container_statuses or []),
    )


def container_status(
    name="main", waiting_reason=None, waiting_message=None, terminated_code=None, terminated_reason=None
):
    waiting = SimpleNamespace(reason=waiting_reason, message=waiting_message) if waiting_reason else None
    terminated = (
        SimpleNamespace(exit_code=terminated_code, reason=terminated_reason) if terminated_code is not None else None
    )
    return SimpleNamespace(name=name, state=SimpleNamespace(waiting=waiting, terminated=terminated))


class FakeWatch:
    def __init__(self, events):
        self._events = events
        self.stopped = False

    def stream(self, *_args, **_kwargs):
        yield from self._events

    def stop(self):
        self.stopped = True


def basic_spec(**overrides):
    defaults = dict(image="alpine", name="squarepeg-alpine-abc123", timeout=5)
    defaults.update(overrides)
    return RunSpec(**defaults)


# --- create_resource ---


def test_create_resource_pod_mode_calls_create_namespaced_pod():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    runner.create_resource(session, basic_spec(mode="pod"), manifest)
    session.core.create_namespaced_pod.assert_called_once_with("default", manifest)
    session.batch.create_namespaced_job.assert_not_called()


def test_create_resource_job_mode_calls_create_namespaced_job():
    session = make_session()
    manifest = {"metadata": {"name": "j"}}
    runner.create_resource(session, basic_spec(mode="job"), manifest)
    session.batch.create_namespaced_job.assert_called_once_with("default", manifest)


def test_create_resource_wraps_api_exception():
    session = make_session()
    session.core.create_namespaced_pod.side_effect = ApiException(status=409, reason="Conflict")
    with pytest.raises(ApiError, match="Conflict"):
        runner.create_resource(session, basic_spec(mode="pod"), {"metadata": {"name": "p"}})


# --- discover_job_pod ---


def test_discover_job_pod_returns_name_from_first_event(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(name="child-pod")}]))
    assert runner.discover_job_pod(session, "myjob", timeout=5) == "child-pod"


def test_discover_job_pod_timeout_raises(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([]))
    with pytest.raises(RunnerError, match="timed out"):
        runner.discover_job_pod(session, "myjob", timeout=5)


# --- wait_until_running_or_terminal ---


def test_wait_returns_on_running_phase(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(phase="Running")}]))
    assert runner.wait_until_running_or_terminal(session, "p", timeout=5) == "Running"


def test_wait_fails_fast_on_image_pull_backoff(monkeypatch):
    session = make_session()
    cs = container_status(waiting_reason="ImagePullBackOff", waiting_message="no such image")
    monkeypatch.setattr(
        runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(phase="Pending", container_statuses=[cs])}])
    )
    with pytest.raises(RunnerError, match="ImagePullBackOff"):
        runner.wait_until_running_or_terminal(session, "p", timeout=5)


def test_wait_timeout_raises(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([]))
    with pytest.raises(RunnerError, match="timed out"):
        runner.wait_until_running_or_terminal(session, "p", timeout=5)


# --- extract_exit_code ---


def test_extract_exit_code_present():
    session = make_session()
    cs = container_status(name="main", terminated_code=0, terminated_reason="Completed")
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[cs])
    code, reason = runner.extract_exit_code(session, "p", "main")
    assert code == 0
    assert reason == "Completed"


def test_extract_exit_code_nonzero():
    session = make_session()
    cs = container_status(name="main", terminated_code=7, terminated_reason="Error")
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[cs])
    code, _ = runner.extract_exit_code(session, "p", "main")
    assert code == 7


def test_extract_exit_code_terminated_none_is_125():
    session = make_session()
    cs = container_status(name="main")  # no waiting, no terminated
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[cs])
    code, reason = runner.extract_exit_code(session, "p", "main")
    assert code == 125
    assert reason is None


def test_extract_exit_code_missing_container_is_125():
    session = make_session()
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[])
    code, _ = runner.extract_exit_code(session, "p", "main")
    assert code == 125


def test_extract_exit_code_picks_named_container_not_index_zero():
    session = make_session()
    sidecar = container_status(name="sidecar", terminated_code=1, terminated_reason="Error")
    main = container_status(name="main", terminated_code=0, terminated_reason="Completed")
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[sidecar, main])
    code, _ = runner.extract_exit_code(session, "p", "main")
    assert code == 0


# --- cleanup ---


def test_cleanup_pod_calls_delete_namespaced_pod():
    session = make_session()
    runner.cleanup(session, basic_spec(mode="pod"), "p")
    session.core.delete_namespaced_pod.assert_called_once_with("p", "default")


def test_cleanup_job_calls_delete_with_propagation_background():
    session = make_session()
    runner.cleanup(session, basic_spec(mode="job"), "j")
    args, kwargs = session.batch.delete_namespaced_job.call_args
    assert args == ("j", "default")
    assert kwargs["body"].propagation_policy == "Background"


def test_cleanup_tolerates_404():
    session = make_session()
    session.core.delete_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")
    runner.cleanup(session, basic_spec(mode="pod"), "p")  # must not raise


def test_cleanup_raises_on_other_errors():
    session = make_session()
    session.core.delete_namespaced_pod.side_effect = ApiException(status=500, reason="Server Error")
    with pytest.raises(ApiError):
        runner.cleanup(session, basic_spec(mode="pod"), "p")


# --- wait_for_terminal ---


def test_wait_for_terminal_returns_on_succeeded(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(phase="Succeeded")}]))
    assert runner.wait_for_terminal(session, "p", threading.Event()) == "Succeeded"


def test_wait_for_terminal_returns_none_when_stopped(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([]))
    stop_event = threading.Event()
    stop_event.set()
    assert runner.wait_for_terminal(session, "p", stop_event) is None


# --- run_manifest orchestration ---


@pytest.fixture(autouse=True)
def _stub_stream_logs(monkeypatch):
    monkeypatch.setattr(runner, "stream_logs", lambda *args, **kwargs: None)


def test_run_manifest_happy_path_calls_in_order(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(phase="Succeeded")}]))
    cs = container_status(name="main", terminated_code=0, terminated_reason="Completed")
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[cs])

    manifest = {"metadata": {"name": "squarepeg-alpine-abc123"}}
    code = runner.run_manifest(session, basic_spec(mode="pod"), manifest)

    assert code == 0
    session.core.create_namespaced_pod.assert_called_once()
    session.core.delete_namespaced_pod.assert_called_once_with("squarepeg-alpine-abc123", "default")


def test_run_manifest_does_not_truncate_logs_for_already_terminal_pod(monkeypatch):
    """Regression test: when the pod is already Succeeded/Failed by the time
    wait_until_running_or_terminal returns (typical for fast-exiting containers), the log
    thread must be allowed to finish reading before stop_event is set -- setting it
    unconditionally right after starting the thread races it and truncates output."""
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(phase="Succeeded")}]))
    cs = container_status(name="main", terminated_code=0, terminated_reason="Completed")
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[cs])

    stop_event_was_set_during_streaming = threading.Event()

    def fake_stream_logs(_session, _pod_name, _container_name, stop_event, quiet=False):
        time.sleep(0.05)  # simulate reading a few lines before the stream naturally EOFs
        if stop_event.is_set():
            stop_event_was_set_during_streaming.set()

    monkeypatch.setattr(runner, "stream_logs", fake_stream_logs)

    manifest = {"metadata": {"name": "squarepeg-alpine-abc123"}}
    code = runner.run_manifest(session, basic_spec(mode="pod"), manifest)

    assert code == 0
    assert not stop_event_was_set_during_streaming.is_set()


def test_run_manifest_job_mode_discovers_pod(monkeypatch):
    session = make_session()
    events = iter(
        [
            FakeWatch([{"object": pod_object(name="child-pod")}]),
            FakeWatch([{"object": pod_object(name="child-pod", phase="Succeeded")}]),
        ]
    )
    monkeypatch.setattr(runner.watch, "Watch", lambda: next(events))
    cs = container_status(name="main", terminated_code=0, terminated_reason="Completed")
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[cs])

    manifest = {"metadata": {"name": "squarepeg-alpine-abc123"}}
    code = runner.run_manifest(session, basic_spec(mode="job"), manifest)

    assert code == 0
    session.batch.create_namespaced_job.assert_called_once()
    session.batch.delete_namespaced_job.assert_called_once()


def test_run_manifest_keep_skips_cleanup(monkeypatch):
    session = make_session()
    monkeypatch.setattr(runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(phase="Succeeded")}]))
    cs = container_status(name="main", terminated_code=0, terminated_reason="Completed")
    session.core.read_namespaced_pod.return_value = pod_object(container_statuses=[cs])

    manifest = {"metadata": {"name": "squarepeg-alpine-abc123"}}
    runner.run_manifest(session, basic_spec(mode="pod", cleanup=False), manifest)

    session.core.delete_namespaced_pod.assert_not_called()


def test_run_manifest_cleanup_called_even_on_wait_failure(monkeypatch):
    session = make_session()
    cs = container_status(waiting_reason="ImagePullBackOff", waiting_message="bad image")
    monkeypatch.setattr(
        runner.watch, "Watch", lambda: FakeWatch([{"object": pod_object(phase="Pending", container_statuses=[cs])}])
    )
    manifest = {"metadata": {"name": "squarepeg-alpine-abc123"}}
    with pytest.raises(RunnerError):
        runner.run_manifest(session, basic_spec(mode="pod"), manifest)
    session.core.delete_namespaced_pod.assert_called_once()


def test_run_manifest_interrupt_deletes_and_raises(monkeypatch):
    session = make_session()

    def wait_and_interrupt(*_args, **_kwargs):
        os.kill(os.getpid(), signal.SIGINT)
        return "Succeeded"

    monkeypatch.setattr(runner, "wait_until_running_or_terminal", wait_and_interrupt)
    manifest = {"metadata": {"name": "squarepeg-alpine-abc123"}}
    with pytest.raises(InterruptError):
        runner.run_manifest(session, basic_spec(mode="pod"), manifest)
    session.core.delete_namespaced_pod.assert_called_once()


def test_run_manifest_second_interrupt_skips_cleanup(monkeypatch):
    session = make_session()

    def wait_and_double_interrupt(*_args, **_kwargs):
        os.kill(os.getpid(), signal.SIGINT)
        os.kill(os.getpid(), signal.SIGINT)
        return "Succeeded"

    monkeypatch.setattr(runner, "wait_until_running_or_terminal", wait_and_double_interrupt)
    manifest = {"metadata": {"name": "squarepeg-alpine-abc123"}}
    with pytest.raises(InterruptError):
        runner.run_manifest(session, basic_spec(mode="pod"), manifest)
    session.core.delete_namespaced_pod.assert_not_called()

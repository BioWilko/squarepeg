import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

from kubernetes.client.rest import ApiException

from squarepeg.k8s import events as events_module
from squarepeg.k8s.events import WarningEventWatcher, print_pod_events


def make_session():
    session = SimpleNamespace()
    session.core = MagicMock()
    session.namespace = "default"
    return session


def event(reason, message, type="Normal"):
    return SimpleNamespace(reason=reason, message=message, type=type)


def test_print_pod_events_calls_chatter_for_each_event(monkeypatch, capsys):
    session = make_session()
    session.core.list_namespaced_event.return_value = SimpleNamespace(
        items=[event("FailedScheduling", "0/12 nodes available"), event("Pulling", "pulling image")]
    )
    print_pod_events(session, "mypod", quiet=False)
    captured = capsys.readouterr()
    assert "FailedScheduling" in captured.err
    assert "0/12 nodes available" in captured.err
    assert "Pulling" in captured.err


def test_print_pod_events_respects_quiet(capsys):
    session = make_session()
    session.core.list_namespaced_event.return_value = SimpleNamespace(items=[event("Pulling", "pulling image")])
    print_pod_events(session, "mypod", quiet=True)
    captured = capsys.readouterr()
    assert captured.err == ""


def test_print_pod_events_swallows_api_errors():
    session = make_session()
    session.core.list_namespaced_event.side_effect = ApiException(status=403, reason="Forbidden")
    print_pod_events(session, "mypod", quiet=False)  # must not raise


def test_print_pod_events_handles_empty_items():
    session = make_session()
    session.core.list_namespaced_event.return_value = SimpleNamespace(items=[])
    print_pod_events(session, "mypod", quiet=False)  # must not raise


def test_print_pod_events_skips_already_shown(capsys):
    session = make_session()
    session.core.list_namespaced_event.return_value = SimpleNamespace(
        items=[event("FailedScheduling", "0/12 nodes available"), event("Pulling", "pulling image")]
    )
    print_pod_events(session, "mypod", skip=frozenset({("FailedScheduling", "0/12 nodes available")}))
    err = capsys.readouterr().err
    assert "0/12 nodes available" not in err
    assert "pulling image" in err


# --- WarningEventWatcher ---

UNSCHEDULABLE = event("FailedScheduling", "0/11 nodes are available: 3 Insufficient nvidia.com/gpu", "Warning")


def test_watcher_prints_each_warning_once_with_one_waiting_note(capsys):
    session = make_session()
    session.core.list_namespaced_event.return_value = SimpleNamespace(
        items=[UNSCHEDULABLE, event("Scheduled", "assigned to node", "Normal")]
    )
    watcher = WarningEventWatcher(session, "mypod", timeout=60)
    watcher.report_new_warnings()
    watcher.report_new_warnings()  # same event again: must not repeat
    err = capsys.readouterr().err
    assert err.count("Insufficient nvidia.com/gpu") == 1
    assert err.count("keep waiting until the 60s --timeout") == 1
    assert "assigned to node" not in err  # Normal events aren't surfaced early
    assert watcher.shown == {("FailedScheduling", UNSCHEDULABLE.message)}


def test_watcher_note_printed_only_for_first_warning(capsys):
    session = make_session()
    session.core.list_namespaced_event.return_value = SimpleNamespace(items=[UNSCHEDULABLE])
    watcher = WarningEventWatcher(session, "mypod", timeout=60)
    watcher.report_new_warnings()
    session.core.list_namespaced_event.return_value = SimpleNamespace(
        items=[UNSCHEDULABLE, event("FailedMount", "pvc not found", "Warning")]
    )
    watcher.report_new_warnings()
    err = capsys.readouterr().err
    assert "pvc not found" in err
    assert err.count("keep waiting") == 1


def test_watcher_polls_in_background(monkeypatch, capsys):
    monkeypatch.setattr(events_module, "EVENT_POLL_SECONDS", 0.01)
    session = make_session()
    polled = threading.Event()
    session.core.list_namespaced_event.side_effect = lambda *a, **kw: (
        polled.set(),
        SimpleNamespace(items=[UNSCHEDULABLE]),
    )[1]
    with WarningEventWatcher(session, "mypod", timeout=60):
        assert polled.wait(2)
    assert "Insufficient nvidia.com/gpu" in capsys.readouterr().err


def test_watcher_quiet_starts_no_thread_and_prints_nothing(capsys):
    session = make_session()
    session.core.list_namespaced_event.return_value = SimpleNamespace(items=[UNSCHEDULABLE])
    with WarningEventWatcher(session, "mypod", timeout=60, quiet=True) as watcher:
        assert watcher._thread is None
    assert capsys.readouterr().err == ""


def test_watcher_swallows_api_errors():
    session = make_session()
    session.core.list_namespaced_event.side_effect = ApiException(status=403, reason="Forbidden")
    WarningEventWatcher(session, "mypod", timeout=60).report_new_warnings()  # must not raise

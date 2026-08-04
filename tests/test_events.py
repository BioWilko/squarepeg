from types import SimpleNamespace
from unittest.mock import MagicMock

from kubernetes.client.rest import ApiException

from squarepeg.k8s.events import print_pod_events


def make_session():
    session = SimpleNamespace()
    session.core = MagicMock()
    session.namespace = "default"
    return session


def event(reason, message):
    return SimpleNamespace(reason=reason, message=message)


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

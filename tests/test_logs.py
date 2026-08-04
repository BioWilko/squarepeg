import threading
from datetime import datetime, timezone

import pytest
from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError
from squarepeg.k8s import logs as logs_module
from squarepeg.k8s.logs import _Dedupe, since_seconds_from, split_timestamp, stream_logs


def test_split_timestamp_splits_prefix():
    ts, content = split_timestamp(b"2024-01-02T03:04:05.123456789Z hello world\n")
    assert ts == "2024-01-02T03:04:05.123456789Z"
    assert content == b"hello world\n"


def test_split_timestamp_no_prefix_returns_none():
    ts, content = split_timestamp(b"hello world\n")
    assert ts is None
    assert content == b"hello world\n"


def test_since_seconds_from_computes_elapsed_plus_one():
    now = datetime(2024, 1, 2, 3, 5, 5, tzinfo=timezone.utc)
    since = since_seconds_from("2024-01-02T03:04:05Z", now=now)
    assert since == 61  # 60s elapsed, +1 buffer


def test_since_seconds_from_never_returns_less_than_one():
    now = datetime(2024, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    since = since_seconds_from("2024-01-02T03:04:05Z", now=now)
    assert since >= 1


class TestDedupe:
    def test_first_line_at_a_timestamp_emits(self):
        d = _Dedupe()
        assert d.should_emit("2024-01-01T00:00:00Z", b"line1") is True

    def test_strictly_newer_timestamp_emits(self):
        d = _Dedupe()
        d.should_emit("2024-01-01T00:00:00Z", b"line1")
        assert d.should_emit("2024-01-01T00:00:01Z", b"line2") is True

    def test_older_timestamp_suppressed(self):
        d = _Dedupe()
        d.should_emit("2024-01-01T00:00:01Z", b"line1")
        assert d.should_emit("2024-01-01T00:00:00Z", b"line0") is False

    def test_duplicate_content_at_same_timestamp_suppressed(self):
        d = _Dedupe()
        d.should_emit("2024-01-01T00:00:00Z", b"line1")
        assert d.should_emit("2024-01-01T00:00:00Z", b"line1") is False

    def test_distinct_content_at_same_timestamp_both_emit(self):
        d = _Dedupe()
        assert d.should_emit("2024-01-01T00:00:00Z", b"line1") is True
        assert d.should_emit("2024-01-01T00:00:00Z", b"line2") is True

    def test_none_timestamp_always_emits(self):
        d = _Dedupe()
        assert d.should_emit(None, b"anything") is True
        assert d.should_emit(None, b"anything") is True


class DiscardSink:
    def write(self, _chunk):
        pass

    def flush(self):
        pass


class FakeResponse:
    def __init__(self, lines, error=None):
        self._lines = lines
        self._error = error
        self.closed = False

    def __iter__(self):
        yield from self._lines
        if self._error is not None:
            raise self._error

    def close(self):
        self.closed = True


def test_stream_logs_writes_bytes_to_sink(monkeypatch):
    session = object()
    responses = iter([FakeResponse([b"2024-01-01T00:00:00Z hello\n", b"2024-01-01T00:00:01Z world\n"])])
    monkeypatch.setattr(logs_module, "_open_stream", lambda *a, **k: next(responses))

    sink_chunks = []
    sink = type("Sink", (), {"write": lambda self, b: sink_chunks.append(b), "flush": lambda self: None})()

    stream_logs(session, "pod", "main", threading.Event(), sink=sink)
    assert b"".join(sink_chunks) == b"hello\nworld\n"


def test_stream_logs_reconnects_after_drop_and_dedupes(monkeypatch):
    session = object()
    responses = iter(
        [
            FakeResponse([b"2024-01-01T00:00:00Z hello\n"], error=ConnectionError("dropped")),
            FakeResponse([b"2024-01-01T00:00:00Z hello\n", b"2024-01-01T00:00:01Z world\n"]),
        ]
    )
    monkeypatch.setattr(logs_module, "_open_stream", lambda *a, **k: next(responses))
    monkeypatch.setattr(logs_module.time, "sleep", lambda _seconds: None)

    sink_chunks = []
    sink = type("Sink", (), {"write": lambda self, b: sink_chunks.append(b), "flush": lambda self: None})()

    stream_logs(session, "pod", "main", threading.Event(), sink=sink)
    assert b"".join(sink_chunks) == b"hello\nworld\n"


def test_stream_logs_gives_up_after_max_retries(monkeypatch):
    session = object()
    monkeypatch.setattr(
        logs_module, "_open_stream", lambda *a, **k: FakeResponse([], error=ConnectionError("still down"))
    )
    monkeypatch.setattr(logs_module.time, "sleep", lambda _seconds: None)

    stream_logs(session, "pod", "main", threading.Event(), sink=DiscardSink())
    # must not raise; just gives up with a warning after MAX_RECONNECT_ATTEMPTS


def test_stream_logs_stops_when_stop_event_set(monkeypatch):
    session = object()
    stop_event = threading.Event()
    stop_event.set()
    monkeypatch.setattr(logs_module, "_open_stream", lambda *a, **k: FakeResponse([b"2024-01-01T00:00:00Z x\n"]))
    stream_logs(session, "pod", "main", stop_event, sink=DiscardSink())


def test_open_stream_wraps_api_exception(monkeypatch):
    session = type("S", (), {"core": type("C", (), {"read_namespaced_pod_log": staticmethod(
        lambda **kwargs: (_ for _ in ()).throw(ApiException(status=404, reason="Not Found"))
    )})(), "namespace": "default"})()
    with pytest.raises(ApiError):
        logs_module._open_stream(session, "pod", "main", None)

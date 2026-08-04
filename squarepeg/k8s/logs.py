"""Follow a pod's logs and write them to stdout, reconnecting on stream drops.

The kubernetes log API only accepts `since_seconds` (not `since_time`), so a
reconnect can only ask for "everything from roughly N seconds ago" -- some
overlap with what was already printed is unavoidable. We request timestamps
internally, strip them before writing (so the user's stdout stays byte-clean
unless --timestamps was passed), and suppress any re-delivered line whose
timestamp is <= the last one we already emitted, breaking ties within the
same timestamp with a small per-timestamp set of already-seen line hashes.
"""

import math
import re
import time
from datetime import datetime, timezone

from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError
from squarepeg.log import chatter

_TIMESTAMP_RE = re.compile(rb"^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z) (.*)$", re.DOTALL)

MAX_RECONNECT_ATTEMPTS = 5
MAX_BACKOFF_SECONDS = 30


def split_timestamp(raw_line: bytes) -> tuple[str | None, bytes]:
    match = _TIMESTAMP_RE.match(raw_line)
    if not match:
        return None, raw_line
    ts, content = match.groups()
    return ts.decode(), content


def _parse_rfc3339(ts: str) -> datetime:
    if ts.endswith("Z"):
        ts = ts[:-1] + "+00:00"
    return datetime.fromisoformat(ts)


def since_seconds_from(last_ts: str, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    elapsed = (now - _parse_rfc3339(last_ts)).total_seconds()
    return max(1, math.ceil(elapsed) + 1)


class _Dedupe:
    """Suppress log lines already emitted, tolerating out-of-order redelivery on reconnect."""

    def __init__(self):
        self.last_ts: str | None = None
        self._seen_at_last_ts: set[bytes] = set()

    def should_emit(self, ts: str | None, content: bytes) -> bool:
        if ts is None:
            return True
        if self.last_ts is None or ts > self.last_ts:
            self.last_ts = ts
            self._seen_at_last_ts = {content}
            return True
        if ts < self.last_ts:
            return False
        if content in self._seen_at_last_ts:
            return False
        self._seen_at_last_ts.add(content)
        return True


def _open_stream(session, pod_name, container_name, since_seconds):
    try:
        return session.core.read_namespaced_pod_log(
            name=pod_name,
            namespace=session.namespace,
            container=container_name,
            follow=True,
            timestamps=True,
            since_seconds=since_seconds,
            _preload_content=False,
        )
    except ApiException as exc:
        raise ApiError(f"failed to open log stream for pod {pod_name!r}: {exc.reason}") from exc


def stream_logs(session, pod_name: str, container_name: str, stop_event, *, quiet: bool = False, sink=None) -> None:
    """Follow the pod's logs, writing raw bytes to `sink` (default sys.stdout.buffer).

    Returns when the stream ends normally (the pod is terminal) or `stop_event` is set.
    Reconnects on a dropped stream, giving up after MAX_RECONNECT_ATTEMPTS with a warning.
    """
    if sink is None:
        import sys

        sink = sys.stdout.buffer

    dedupe = _Dedupe()
    since_seconds = None
    attempt = 0

    while not stop_event.is_set():
        response = _open_stream(session, pod_name, container_name, since_seconds)
        try:
            for raw_line in response:
                if stop_event.is_set():
                    return
                ts, content = split_timestamp(raw_line)
                if dedupe.should_emit(ts, content):
                    sink.write(content if content.endswith(b"\n") else content + b"\n")
                    sink.flush()
            return  # clean EOF: the stream ended on its own
        except Exception as exc:  # any transport error triggers a reconnect attempt
            attempt += 1
            if attempt > MAX_RECONNECT_ATTEMPTS:
                chatter(
                    f"log stream for pod {pod_name!r} failed after {attempt - 1} reconnect attempts ({exc}); "
                    "still waiting for the pod to finish",
                    quiet=quiet,
                )
                return
            if dedupe.last_ts is not None:
                since_seconds = since_seconds_from(dedupe.last_ts)
            backoff = min(2**attempt, MAX_BACKOFF_SECONDS)
            chatter(f"log stream for pod {pod_name!r} dropped, reconnecting in {backoff}s", quiet=quiet)
            time.sleep(backoff)
        finally:
            response.close()

"""Surface pod events as diagnostics: the difference between 'it hung' and
'0/12 nodes available: insufficient memory'.
"""

import threading

from kubernetes.client.rest import ApiException

from squarepeg.log import chatter

# how often WarningEventWatcher re-lists events while waiting for a pod to start
EVENT_POLL_SECONDS = 3


def _list_events(session, name: str) -> list:
    try:
        events = session.core.list_namespaced_event(
            session.namespace, field_selector=f"involvedObject.name={name}"
        )
    except ApiException:
        return []  # best-effort diagnostics; never fail the run because event listing failed
    return list(events.items or [])


def _event_key(event) -> tuple[str, str]:
    return (event.reason, event.message)


def print_pod_events(
    session, pod_name: str, *, quiet: bool = False, skip: frozenset[tuple[str, str]] = frozenset()
) -> None:
    """Print every event for `pod_name`, except those already shown (by (reason, message))."""
    for event in _list_events(session, pod_name):
        if _event_key(event) not in skip:
            chatter(f"{pod_name}: {event.reason}: {event.message}", quiet=quiet, level="detail")


class WarningEventWatcher:
    """Context manager that, while squarepeg waits on `name` (a pod, or a job waiting to
    create one), polls its Warning events in the background and prints each new one as
    soon as it appears.

    These are usually the real answer to "why isn't it starting?" -- an unschedulable pod,
    a missing RuntimeClass, an exceeded quota, a volume that won't mount -- and without
    this they'd only be printed once --timeout had already expired. squarepeg still waits
    out the timeout rather than failing on them, since some do resolve on their own (a
    cluster autoscaler adding a node, a PVC binding), and says so once.
    """

    def __init__(self, session, name: str, *, timeout: int, quiet: bool = False):
        self.session = session
        self.name = name
        self.timeout = timeout
        self.quiet = quiet
        self.shown: set[tuple[str, str]] = set()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def __enter__(self) -> "WarningEventWatcher":
        if not self.quiet:
            self._thread = threading.Thread(target=self._poll, daemon=True)
            self._thread.start()
        return self

    def __exit__(self, *_exc) -> bool:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2)
        return False

    def _poll(self) -> None:
        while not self._stop.wait(EVENT_POLL_SECONDS):
            self.report_new_warnings()

    def report_new_warnings(self) -> None:
        for event in _list_events(self.session, self.name):
            key = _event_key(event)
            if event.type != "Warning" or key in self.shown:
                continue
            first = not self.shown
            self.shown.add(key)
            chatter(f"{self.name}: {event.reason}: {event.message}", quiet=self.quiet, level="warn")
            if first:
                chatter(
                    f"this may not resolve on its own, but squarepeg will keep waiting until the "
                    f"{self.timeout}s --timeout in case it does (e.g. a node scaling up); "
                    "Ctrl+C to give up now",
                    quiet=self.quiet,
                    level="detail",
                )

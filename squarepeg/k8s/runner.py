"""Create a Pod/Job, stream its logs, wait for it to reach a terminal state,
extract its exit code, and clean up.
"""

import signal
import threading

from kubernetes import watch
from kubernetes.client.rest import ApiException

from squarepeg import ui
from squarepeg.errors import InterruptError, RunnerError
from squarepeg.k8s.apierrors import delete_resource, wrap_api_exception
from squarepeg.k8s.events import print_pod_events
from squarepeg.k8s.logs import stream_logs
from squarepeg.k8s.orphans import sweep_orphans
from squarepeg.k8s.podstate import FAIL_FAST_REASONS
from squarepeg.labels import RUN_ID_LABEL
from squarepeg.log import chatter
from squarepeg.runspec import RunSpec

# kept as module-level aliases: existing callers/tests referencing these names still work
_wrap_api_exception = wrap_api_exception

# how long a single watch call may block waiting for the pod to become terminal,
# once it has already started; the loop simply re-watches if this elapses.
TERMINAL_WATCH_POLL_SECONDS = 3600


def create_resource(session, spec: RunSpec, manifest: dict) -> None:
    name = manifest["metadata"]["name"]
    try:
        if spec.mode == "job":
            session.batch.create_namespaced_job(session.namespace, manifest)
        else:
            session.core.create_namespaced_pod(session.namespace, manifest)
    except ApiException as exc:
        raise wrap_api_exception(exc, f"failed to create {spec.mode} {name!r}") from exc


def discover_job_pod(session, job_name: str, timeout: int) -> str:
    watcher = watch.Watch()
    try:
        for event in watcher.stream(
            session.core.list_namespaced_pod,
            namespace=session.namespace,
            label_selector=f"job-name={job_name}",
            timeout_seconds=timeout,
        ):
            return event["object"].metadata.name
    except ApiException as exc:
        raise _wrap_api_exception(exc, f"failed to watch for the pod created by job {job_name!r}") from exc
    finally:
        watcher.stop()
    raise RunnerError(f"timed out waiting for job {job_name!r} to create a pod")


def wait_until_running_or_terminal(session, pod_name: str, timeout: int, *, quiet: bool = False) -> str:
    """Wait until the pod is Running/Succeeded/Failed, or fail fast on a known-bad waiting reason.

    This bounds only the *startup* window (image pull, scheduling, container create); once the
    container is actually running, log streaming plus wait_for_terminal take over with no timeout.
    """
    try:
        return _watch_for_start(session, pod_name, timeout)
    except RunnerError:
        print_pod_events(session, pod_name, quiet=quiet)
        raise


def _watch_for_start(session, pod_name: str, timeout: int) -> str:
    watcher = watch.Watch()
    try:
        for event in watcher.stream(
            session.core.list_namespaced_pod,
            namespace=session.namespace,
            field_selector=f"metadata.name={pod_name}",
            timeout_seconds=timeout,
        ):
            pod = event["object"]
            phase = pod.status.phase
            for cs in pod.status.container_statuses or []:
                waiting = cs.state.waiting
                if waiting is not None and waiting.reason in FAIL_FAST_REASONS:
                    raise RunnerError(f"container failed to start ({waiting.reason}): {waiting.message}")
            if phase in ("Running", "Succeeded", "Failed"):
                return phase
    except ApiException as exc:
        raise _wrap_api_exception(exc, f"failed to watch pod {pod_name!r}") from exc
    finally:
        watcher.stop()
    raise RunnerError(f"timed out after {timeout}s waiting for pod {pod_name!r} to start")


def wait_for_terminal(session, pod_name: str, stop_event: threading.Event) -> str | None:
    """Wait, with no overall timeout, until the pod is Succeeded/Failed, or stop_event is set."""
    while not stop_event.is_set():
        watcher = watch.Watch()
        try:
            for event in watcher.stream(
                session.core.list_namespaced_pod,
                namespace=session.namespace,
                field_selector=f"metadata.name={pod_name}",
                timeout_seconds=TERMINAL_WATCH_POLL_SECONDS,
            ):
                if stop_event.is_set():
                    return None
                phase = event["object"].status.phase
                if phase in ("Succeeded", "Failed"):
                    return phase
        except ApiException as exc:
            raise _wrap_api_exception(exc, f"failed to watch pod {pod_name!r}") from exc
        finally:
            watcher.stop()
        # the watch call's own timeout elapsed with no terminal event yet; just re-watch
    return None


def extract_exit_code(session, pod_name: str, container_name: str) -> tuple[int, str | None]:
    try:
        pod = session.core.read_namespaced_pod(pod_name, session.namespace)
    except ApiException as exc:
        raise _wrap_api_exception(exc, f"failed to read pod {pod_name!r}") from exc

    for cs in pod.status.container_statuses or []:
        if cs.name != container_name:
            continue
        terminated = cs.state.terminated
        if terminated is None:
            return 125, None
        return terminated.exit_code, terminated.reason
    return 125, None


def cleanup(session, spec: RunSpec, name: str) -> None:
    delete_resource(session, spec.mode, name)


class _InterruptHandler:
    """Two-stage Ctrl+C: first press cleans up and stops; second press abandons immediately."""

    def __init__(self, name: str, mode: str, quiet: bool):
        self.name = name
        self.mode = mode
        self.quiet = quiet
        self.count = 0
        self.stop_event = threading.Event()
        self._previous = {}

    def __enter__(self):
        for sig in (signal.SIGINT, signal.SIGTERM):
            self._previous[sig] = signal.getsignal(sig)
            signal.signal(sig, self._handle)
        return self

    def __exit__(self, *_exc_info):
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)
        return False

    def _handle(self, _signum, _frame):
        self.count += 1
        self.stop_event.set()
        if self.count == 1:
            chatter(
                f"interrupted; cleaning up {self.mode} {self.name!r} (press Ctrl+C again to leave it running)",
                quiet=self.quiet,
                level="warn",
            )
        else:
            chatter(f"leaving {self.mode} {self.name!r} running", quiet=False, level="warn")

    @property
    def interrupted(self) -> bool:
        return self.count > 0

    @property
    def abandoned(self) -> bool:
        return self.count >= 2


def run_manifest(session, spec: RunSpec, manifest: dict) -> int:
    """Create, stream logs, wait for a terminal state, extract the exit code, and clean up."""
    name = manifest["metadata"]["name"]

    with _InterruptHandler(name, spec.mode, spec.quiet) as handler:
        log_thread = None
        try:
            with ui.Status(f"creating {spec.mode} {name!r} in namespace {session.namespace!r}", quiet=spec.quiet):
                create_resource(session, spec, manifest)

            if spec.mode == "job":
                with ui.Status(
                    f"waiting for job {name!r} to create a pod",
                    quiet=spec.quiet,
                    slow_hint="the job controller has not produced a pod yet",
                    slow_after=15,
                ):
                    pod_name = discover_job_pod(session, name, spec.timeout)
            else:
                pod_name = name

            with ui.Status(
                f"waiting for pod {pod_name!r} to start",
                quiet=spec.quiet,
                slow_hint="still scheduling or pulling the image; a cold pull of a large image can take a while",
                slow_after=20,
                spinner_delay=0.0,
            ):
                phase = wait_until_running_or_terminal(session, pod_name, spec.timeout, quiet=spec.quiet)

            chatter(
                f"streaming logs from pod {pod_name!r} (waiting for it to finish; Ctrl+C to stop)",
                quiet=spec.quiet,
                level="step",
            )

            log_thread = threading.Thread(
                target=stream_logs,
                args=(session, pod_name, spec.container_name, handler.stop_event),
                kwargs={"quiet": spec.quiet},
                daemon=True,
            )
            log_thread.start()

            if phase == "Running":
                wait_for_terminal(session, pod_name, handler.stop_event)

            # let the log stream reach its own natural EOF (the kubelet closes it once the
            # container is terminal); only force it to stop if it's genuinely stuck, since
            # setting stop_event unconditionally here would race a just-started thread and
            # truncate output for fast-exiting containers.
            log_thread.join(timeout=30)
            if log_thread.is_alive():
                chatter(
                    f"log stream for pod {pod_name!r} did not finish on its own; stopping it",
                    quiet=spec.quiet,
                    level="warn",
                )
                handler.stop_event.set()
                log_thread.join(timeout=5)

            if handler.interrupted:
                raise InterruptError(f"interrupted while running {spec.mode} {name!r}")

            exit_code, reason = extract_exit_code(session, pod_name, spec.container_name)
            if reason == "OOMKilled":
                chatter(f"container was OOMKilled (exit code {exit_code})", quiet=spec.quiet, level="error")
            return exit_code
        finally:
            handler.stop_event.set()
            if log_thread is not None:
                log_thread.join(timeout=2)
            if spec.cleanup and not handler.abandoned:
                with ui.Status(f"deleting {spec.mode} {name!r}", quiet=spec.quiet):
                    cleanup(session, spec, name)
            elif not handler.abandoned and not spec.quiet:
                chatter(
                    f"kept {spec.mode} {name!r}; inspect with 'kubectl describe {spec.mode} {name}'",
                    quiet=False,
                    level="warn",
                )
            if spec.orphan_sweep and not handler.abandoned:
                own_run_id = manifest.get("metadata", {}).get("labels", {}).get(RUN_ID_LABEL)
                try:
                    with ui.Status("sweeping orphaned resources from previous runs", quiet=spec.quiet):
                        sweep_orphans(session, spec, exclude_run_id=own_run_id)
                except Exception as exc:
                    chatter(f"orphan sweep skipped: {exc}", quiet=spec.quiet, level="warn")

"""Create a Pod/Job, stream its logs, wait for it to reach a terminal state,
extract its exit code, and clean up.
"""

import json
import signal
import threading

from kubernetes import client, watch
from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError, InterruptError, RunnerError
from squarepeg.k8s.events import print_pod_events
from squarepeg.k8s.logs import stream_logs
from squarepeg.log import chatter
from squarepeg.runspec import RunSpec

FAIL_FAST_REASONS = {
    "ImagePullBackOff",
    "ErrImagePull",
    "InvalidImageName",
    "CreateContainerConfigError",
    "CreateContainerError",
    "RunContainerError",
    "CrashLoopBackOff",
}

_API_ERROR_HINTS = {
    400: "the apiserver rejected the manifest as malformed; run with --dry-run to inspect it",
    401: "authentication failed; try 'kubectl get pods' to check your credentials",
    403: "permission denied; check with 'kubectl auth can-i create pods'",
    404: "not found; check the namespace exists",
    409: "already exists; omit --name, or clean up a --keep'd previous run",
    422: "the apiserver rejected the manifest; run with --dry-run to inspect it",
}

# A ResourceQuota admission rejection also arrives as HTTP 403, but it has nothing to do with
# RBAC permissions -- the generic 403 hint above ("check with kubectl auth can-i") is actively
# misleading here. Detected by the admission plugin's standard "failed quota: ..." message
# text, which names the missing requests/limits per container.
_QUOTA_ERROR_MARKER = "failed quota"
_QUOTA_HINT = (
    "the target namespace enforces a ResourceQuota requiring cpu/memory requests (and "
    "possibly limits) on every container; set --cpus/--memory (or --request-cpu/--request-"
    "memory/--limit-cpu/--limit-memory), or 'defaults.cpus'/'defaults.memory' in config so "
    "you don't have to pass them on every run"
)

# how long a single watch call may block waiting for the pod to become terminal,
# once it has already started; the loop simply re-watches if this elapses.
TERMINAL_WATCH_POLL_SECONDS = 3600


def _api_exception_detail(exc: ApiException) -> str:
    """Extract the Kubernetes Status object's 'message' field from the response body, if
    present -- this is almost always far more specific than exc.reason (the bare HTTP
    reason phrase, e.g. 'Bad Request'), naming the exact field the apiserver rejected."""
    if exc.body:
        try:
            body = json.loads(exc.body)
        except (TypeError, ValueError):
            body = None
        if isinstance(body, dict) and body.get("message"):
            return body["message"]
    return exc.reason


def _wrap_api_exception(exc: ApiException, action: str) -> ApiError:
    detail = _api_exception_detail(exc)
    if exc.status == 403 and _QUOTA_ERROR_MARKER in detail.lower():
        hint = _QUOTA_HINT
    else:
        hint = _API_ERROR_HINTS.get(exc.status)
    message = f"{action}: {detail}"
    if hint:
        message += f" ({hint})"
    return ApiError(message)


def create_resource(session, spec: RunSpec, manifest: dict) -> None:
    name = manifest["metadata"]["name"]
    try:
        if spec.mode == "job":
            session.batch.create_namespaced_job(session.namespace, manifest)
        else:
            session.core.create_namespaced_pod(session.namespace, manifest)
    except ApiException as exc:
        raise _wrap_api_exception(exc, f"failed to create {spec.mode} {name!r}") from exc


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
    try:
        if spec.mode == "job":
            session.batch.delete_namespaced_job(
                name, session.namespace, body=client.V1DeleteOptions(propagation_policy="Background")
            )
        else:
            session.core.delete_namespaced_pod(name, session.namespace)
    except ApiException as exc:
        if exc.status == 404:
            return
        raise _wrap_api_exception(exc, f"failed to delete {spec.mode} {name!r}") from exc


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
            )
        else:
            chatter(f"leaving {self.mode} {self.name!r} running", quiet=False)

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
            create_resource(session, spec, manifest)
            pod_name = discover_job_pod(session, name, spec.timeout) if spec.mode == "job" else name

            phase = wait_until_running_or_terminal(session, pod_name, spec.timeout, quiet=spec.quiet)

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
                chatter(f"log stream for pod {pod_name!r} did not finish on its own; stopping it", quiet=spec.quiet)
                handler.stop_event.set()
                log_thread.join(timeout=5)

            if handler.interrupted:
                raise InterruptError(f"interrupted while running {spec.mode} {name!r}")

            exit_code, reason = extract_exit_code(session, pod_name, spec.container_name)
            if reason == "OOMKilled":
                chatter(f"container was OOMKilled (exit code {exit_code})", quiet=spec.quiet)
            return exit_code
        finally:
            handler.stop_event.set()
            if log_thread is not None:
                log_thread.join(timeout=2)
            if spec.cleanup and not handler.abandoned:
                cleanup(session, spec, name)
            elif not handler.abandoned and not spec.quiet:
                chatter(f"kept {spec.mode} {name!r}; inspect with 'kubectl describe {spec.mode} {name}'", quiet=False)

"""Shared vocabulary for interpreting Pod/Job status: phase names, the fail-fast
waiting-reason set, and the timestamp/age arithmetic used both by the normal
startup-wait path (runner.py) and by the orphan sweep (orphans.py).

Kept as a single source of truth deliberately: if runner.py's "give up on this
container" judgment and the sweep's "this pod is stuck forever" judgment ever
used two different copies of the fail-fast reason set, a future addition to one
would silently fail to apply to the other -- exactly the kind of divergence
that's invisible until an orphan doesn't get swept.
"""

from datetime import datetime, timezone

TERMINAL_PHASES = frozenset({"Succeeded", "Failed"})

FAIL_FAST_REASONS = frozenset(
    {
        "ImagePullBackOff",
        "ErrImagePull",
        "InvalidImageName",
        "CreateContainerConfigError",
        "CreateContainerError",
        "RunContainerError",
        "CrashLoopBackOff",
    }
)


def stuck_waiting_reason(pod) -> str | None:
    """The first container waiting-reason that's in FAIL_FAST_REASONS, if any."""
    for cs in pod.status.container_statuses or []:
        waiting = cs.state.waiting
        if waiting is not None and waiting.reason in FAIL_FAST_REASONS:
            return waiting.reason
    return None


def _as_aware_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _age_seconds(timestamp: datetime | None, now: datetime) -> float | None:
    if timestamp is None:
        return None
    return max(0.0, (now - _as_aware_utc(timestamp)).total_seconds())


def terminal_age_seconds(pod, now: datetime) -> float | None:
    """Seconds since a Succeeded/Failed pod actually became terminal.

    Uses the latest container terminated.finished_at across all containers (a pod only
    reaches a terminal phase once every container has exited, so the max is the true
    moment of termination, and it's also the most conservative choice -- the smallest
    resulting age). Falls back to creation_timestamp if no container has that info at
    all; safe because the phase gate (not this timestamp) is what proves the pod is dead.
    """
    finished_ats = [
        cs.state.terminated.finished_at
        for cs in (pod.status.container_statuses or [])
        if cs.state.terminated is not None and cs.state.terminated.finished_at is not None
    ]
    if finished_ats:
        return _age_seconds(max(finished_ats), now)
    return _age_seconds(pod.metadata.creation_timestamp, now)


def pod_age_seconds(pod, now: datetime) -> float | None:
    """Seconds since a pod was created.

    This is the only sensible age basis for a pod stuck in Pending on a fail-fast waiting
    reason: it never terminated (so there's no terminated.finished_at anywhere), and
    Kubernetes' ContainerStateWaiting carries no timestamp at all -- there is nothing else
    to measure from, and "time since creation" is exactly the question being asked
    ("how long has this thing existed without ever managing to start?").
    """
    return _age_seconds(pod.metadata.creation_timestamp, now)


def job_terminal_condition(job):
    """The Job's Complete/Failed condition (status == 'True'), if it has reached one."""
    for condition in job.status.conditions or []:
        if condition.type in ("Complete", "Failed") and condition.status == "True":
            return condition
    return None


def job_terminal_age_seconds(job, now: datetime) -> float | None:
    condition = job_terminal_condition(job)
    if condition is None:
        return None
    return _age_seconds(condition.last_transition_time, now)

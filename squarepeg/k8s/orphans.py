"""Opportunistic sweep for orphaned squarepeg-managed Pods/Jobs left behind by a
previous invocation that crashed or was killed before it could clean up after
itself (machine died, kill -9, network partition, ...).

Runs alongside -- never instead of -- the per-run cleanup in runner.py, and is
scoped strictly to the CURRENT user's own resources (never a teammate's, even
in a shared namespace) via the server-side label selector.

Two independent categories of orphan, because a resource that never managed to
start looks nothing like one that finished and wasn't deleted:

  - terminal:  Succeeded/Failed, older than the threshold since it finished.
  - stuck:     Pending with a container waiting on a reason squarepeg already
               treats as fatal-on-sight (see podstate.FAIL_FAST_REASONS -- the
               same set runner.py uses to fail fast on ITS OWN pod). A bare
               Pod has no backoffLimit/TTL, so a pod stuck like this (e.g. a
               typo'd image reference) never reaches Succeeded/Failed and
               would otherwise sit in the namespace forever.

Never anything in Running/Pending-without-a-fail-fast-reason/Unknown, at any
age -- that's the load-bearing safety property of the whole feature.
"""

from datetime import datetime, timezone

from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError
from squarepeg.k8s.apierrors import delete_resource, wrap_api_exception
from squarepeg.k8s.podstate import (
    TERMINAL_PHASES,
    job_terminal_age_seconds,
    pod_age_seconds,
    stuck_waiting_reason,
    terminal_age_seconds,
)
from squarepeg.labels import (
    CREATED_BY_LABEL,
    KEEP_LABEL,
    MANAGED_BY_LABEL,
    MANAGED_BY_VALUE,
    RUN_ID_LABEL,
    current_created_by,
)
from squarepeg.log import chatter
from squarepeg.runspec import RunSpec

DEFAULT_MIN_AGE_SECONDS = 300


def _label_selector(created_by: str) -> str:
    return f"{MANAGED_BY_LABEL}={MANAGED_BY_VALUE},{CREATED_BY_LABEL}={created_by},!{KEEP_LABEL}"


def _job_name_for_pod(pod) -> str | None:
    labels = pod.metadata.labels or {}
    if "job-name" in labels:
        return labels["job-name"]
    for ref in pod.metadata.owner_references or []:
        if ref.kind == "Job":
            return ref.name
    return None


def _is_own_and_unexcluded(labels: dict, created_by: str, exclude_run_id: str | None) -> bool:
    if labels.get(CREATED_BY_LABEL) != created_by:
        return False
    if exclude_run_id is not None and labels.get(RUN_ID_LABEL) == exclude_run_id:
        return False
    # belt-and-braces: the server-side selector already excludes anything carrying the keep
    # label, but re-checking client-side means a selector typo can never turn into deleting
    # a resource the user explicitly asked to keep.
    if labels.get(KEEP_LABEL):
        return False
    return True


def _sweepable_pod_age(pod, min_age_seconds: int, now: datetime) -> bool:
    """True if this pod (already confirmed own-user/not-excluded) is old enough to sweep,
    under either the terminal or the stuck-before-starting category."""
    phase = pod.status.phase
    if phase in TERMINAL_PHASES:
        age = terminal_age_seconds(pod, now)
    elif phase == "Pending" and stuck_waiting_reason(pod) is not None:
        age = pod_age_seconds(pod, now)
    else:
        return False
    return age is not None and age >= min_age_seconds


def _find_pods(pods, created_by: str, min_age_seconds: int, exclude_run_id: str | None, now: datetime):
    """Returns (bare_pod_candidates, stuck_child_job_names).

    Job-owned pods are never deleted directly -- deleting a Job's child pod while the Job
    still has a non-zero backoffLimit would make the controller spawn a replacement, i.e.
    the sweep would start work rather than clean it up. Instead, a Job-owned pod that would
    otherwise qualify is reported back so the caller can delete the parent Job instead,
    which cascades to the pod via Background propagation.
    """
    bare_candidates = []
    stuck_child_job_names = set()

    for pod in pods:
        labels = pod.metadata.labels or {}
        if not _is_own_and_unexcluded(labels, created_by, exclude_run_id):
            continue
        if not _sweepable_pod_age(pod, min_age_seconds, now):
            continue

        job_name = _job_name_for_pod(pod)
        if job_name is not None:
            stuck_child_job_names.add(job_name)
        else:
            bare_candidates.append(("pod", pod.metadata.name))

    return bare_candidates, stuck_child_job_names


def _find_jobs(
    jobs, created_by: str, min_age_seconds: int, exclude_run_id: str | None, now: datetime, stuck_child_job_names: set
):
    candidates = []
    for job in jobs:
        labels = job.metadata.labels or {}
        if not _is_own_and_unexcluded(labels, created_by, exclude_run_id):
            continue

        name = job.metadata.name
        age = job_terminal_age_seconds(job, now)
        if age is not None and age >= min_age_seconds:
            candidates.append(("job", name))
        elif name in stuck_child_job_names:
            # no Complete/Failed condition yet (e.g. backoffLimit: 0 means a stuck pull
            # never produces a failure condition at all), but its child pod has been
            # stuck on a fail-fast reason long enough -- same verdict, different signal.
            candidates.append(("job", name))
    return candidates


def find_orphaned_resources(
    session,
    created_by: str,
    min_age_seconds: int,
    *,
    exclude_run_id: str | None = None,
    now: datetime | None = None,
) -> list[tuple[str, str]]:
    """Discover this user's own orphaned Pods/Jobs.

    Server-side scoped to app.kubernetes.io/managed-by=squarepeg, squarepeg.io/created-by
    matching `created_by`, and NOT carrying squarepeg.io/keep -- so a teammate's resource or
    a deliberately --keep'd one is never even returned by the apiserver. Client-side filtered
    by phase/waiting-reason and age. Returns [(kind, name), ...]; deletion is identical for
    every entry regardless of which category matched.

    `now` defaults to the real current time; tests pass a fixed value.
    """
    now = now or datetime.now(timezone.utc)
    selector = _label_selector(created_by)

    pods = session.core.list_namespaced_pod(session.namespace, label_selector=selector).items
    jobs = session.batch.list_namespaced_job(session.namespace, label_selector=selector).items

    bare_pod_candidates, stuck_child_job_names = _find_pods(pods, created_by, min_age_seconds, exclude_run_id, now)
    job_candidates = _find_jobs(jobs, created_by, min_age_seconds, exclude_run_id, now, stuck_child_job_names)
    return bare_pod_candidates + job_candidates


def sweep_orphans(
    session, spec: RunSpec, *, exclude_run_id: str | None = None, now: datetime | None = None
) -> int:
    """Discover and delete this user's own orphaned resources.

    Best-effort throughout: a per-resource delete failure is logged and skipped rather than
    raised, so one undeletable orphan never aborts the rest of the sweep. The caller
    (run_manifest) is responsible for making sure a failure here can never change the
    current invocation's own exit code -- see runner.py's finally block.
    """
    created_by = current_created_by()
    try:
        candidates = find_orphaned_resources(
            session, created_by, spec.orphan_sweep_min_age, exclude_run_id=exclude_run_id, now=now
        )
    except ApiException as exc:
        wrapped = wrap_api_exception(exc, "failed to list resources for orphan sweep")
        raise ApiError(f"{wrapped} (set 'orphan_sweep: false' in config to disable)") from exc

    deleted = 0
    for kind, name in candidates:
        try:
            if delete_resource(session, kind, name):
                deleted += 1
        except Exception as exc:  # one bad delete must not abort the rest of the sweep
            chatter(f"orphan sweep: failed to delete {kind} {name!r}: {exc}", quiet=spec.quiet, level="warn")

    if deleted:
        chatter(f"swept {deleted} orphaned resource(s) from previous runs", quiet=spec.quiet, level="success")
    return deleted

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError
from squarepeg.k8s import orphans
from squarepeg.runspec import RunSpec

NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
CREATED_BY = "sam"


def make_session(pods=(), jobs=()):
    session = SimpleNamespace()
    session.namespace = "default"
    session.core = MagicMock()
    session.batch = MagicMock()
    session.core.list_namespaced_pod.return_value = SimpleNamespace(items=list(pods))
    session.batch.list_namespaced_job.return_value = SimpleNamespace(items=list(jobs))
    return session


def owner_ref(kind, name):
    return SimpleNamespace(kind=kind, name=name)


def container_status(waiting_reason=None, finished_at=None):
    waiting = SimpleNamespace(reason=waiting_reason) if waiting_reason else None
    terminated = SimpleNamespace(finished_at=finished_at) if finished_at is not None else None
    return SimpleNamespace(state=SimpleNamespace(waiting=waiting, terminated=terminated))


def finished(seconds_ago):
    """A single-container status list for a pod that terminated `seconds_ago` seconds ago."""
    return [container_status(finished_at=NOW - timedelta(seconds=seconds_ago))]


def waiting(reason):
    """A single-container status list for a pod stuck waiting on `reason`."""
    return [container_status(reason)]


def fake_pod(
    name,
    *,
    phase="Running",
    created_by=CREATED_BY,
    run_id="other-run-id",
    keep=False,
    container_statuses=None,
    creation_offset=None,
    job_name=None,
    owner_kind=None,
):
    labels = {"app.kubernetes.io/managed-by": "squarepeg", "squarepeg.io/created-by": created_by}
    if run_id is not None:
        labels["squarepeg.io/run-id"] = run_id
    if keep:
        labels["squarepeg.io/keep"] = "true"
    if job_name:
        labels["job-name"] = job_name
    owner_refs = [owner_ref(owner_kind, job_name or "some-job")] if owner_kind else []
    creation = NOW - timedelta(seconds=creation_offset) if creation_offset is not None else None
    return SimpleNamespace(
        metadata=SimpleNamespace(
            name=name, labels=labels, owner_references=owner_refs, creation_timestamp=creation
        ),
        status=SimpleNamespace(phase=phase, container_statuses=container_statuses or []),
    )


def fake_job(name, *, created_by=CREATED_BY, run_id="other-run-id", keep=False, conditions=None):
    labels = {"app.kubernetes.io/managed-by": "squarepeg", "squarepeg.io/created-by": created_by}
    if run_id is not None:
        labels["squarepeg.io/run-id"] = run_id
    if keep:
        labels["squarepeg.io/keep"] = "true"
    return SimpleNamespace(
        metadata=SimpleNamespace(name=name, labels=labels),
        status=SimpleNamespace(conditions=conditions or []),
    )


def condition(type_, status="True", last_transition_time=None):
    return SimpleNamespace(type=type_, status=status, last_transition_time=last_transition_time)


def basic_spec(**overrides):
    defaults = dict(image="alpine", name="squarepeg-alpine-abc123", orphan_sweep_min_age=300)
    defaults.update(overrides)
    return RunSpec(**defaults)


# --- selector construction ---


def test_selector_contains_all_three_clauses():
    session = make_session()
    orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW)
    pod_kwargs = session.core.list_namespaced_pod.call_args
    job_kwargs = session.batch.list_namespaced_job.call_args
    for call in (pod_kwargs, job_kwargs):
        args, kwargs = call
        assert args[0] == "default"
        selector = kwargs["label_selector"]
        assert "app.kubernetes.io/managed-by=squarepeg" in selector
        assert f"squarepeg.io/created-by={CREATED_BY}" in selector
        assert "!squarepeg.io/keep" in selector


# --- mixed-list narrowing ---


def test_mixed_list_narrows_to_sweepable_only():
    pods = [
        fake_pod("running", phase="Running", creation_offset=1000),
        fake_pod(
            "pending-benign", phase="Pending", container_statuses=waiting("ContainerCreating"), creation_offset=1000
        ),
        fake_pod("stuck-old", phase="Pending", container_statuses=waiting("ImagePullBackOff"), creation_offset=600),
        fake_pod("unknown", phase="Unknown", creation_offset=1000),
        fake_pod("succeeded-young", phase="Succeeded", container_statuses=finished(10)),
        fake_pod("succeeded-old", phase="Succeeded", container_statuses=finished(600)),
        fake_pod("failed-old", phase="Failed", container_statuses=finished(600)),
        fake_pod("job-owned", phase="Succeeded", job_name="somejob", container_statuses=finished(600)),
        fake_pod("owner-ref-job", phase="Succeeded", owner_kind="Job", container_statuses=finished(600)),
    ]
    session = make_session(pods=pods)
    result = orphans.find_orphaned_resources(session, CREATED_BY, 300, exclude_run_id="current-run", now=NOW)
    names = {name for kind, name in result if kind == "pod"}
    assert names == {"succeeded-old", "failed-old", "stuck-old"}


def test_keep_label_excluded_client_side_even_if_server_returns_it():
    pods = [
        fake_pod("kept-terminal", phase="Succeeded", keep=True, container_statuses=finished(600)),
        fake_pod(
            "kept-stuck",
            phase="Pending",
            keep=True,
            container_statuses=waiting("ImagePullBackOff"),
            creation_offset=600,
        ),
    ]
    # NOTE: real server-side filtering would never return these; this simulates a selector
    # bug and proves the client-side check is a real backstop, not decorative.
    session = make_session(pods=pods)
    # the fake session doesn't itself filter by selector, so both pods "leak" through --
    # but find_orphaned_resources must still exclude them by reading the keep label itself.
    result = orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW)
    assert result == []


def test_ownership_scope_excludes_other_users_pods():
    pods = [
        fake_pod("someone-elses", phase="Succeeded", created_by="alex", container_statuses=finished(600)),
    ]
    session = make_session(pods=pods)
    result = orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW)
    assert result == []


# --- category B: stuck-before-starting ---


def test_stuck_pod_swept():
    pods = [fake_pod("stuck", phase="Pending", container_statuses=waiting("ImagePullBackOff"), creation_offset=600)]
    session = make_session(pods=pods)
    result = orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW)
    assert result == [("pod", "stuck")]


@pytest.mark.parametrize(
    "reason",
    ["ImagePullBackOff", "ErrImagePull", "InvalidImageName", "CreateContainerConfigError", "CreateContainerError"],
)
def test_stuck_pod_swept_for_every_pending_reachable_reason(reason):
    pods = [fake_pod("stuck", phase="Pending", container_statuses=waiting(reason), creation_offset=600)]
    session = make_session(pods=pods)
    result = orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW)
    assert result == [("pod", "stuck")]


def test_stuck_pod_young_not_swept():
    pods = [fake_pod("stuck", phase="Pending", container_statuses=waiting("ImagePullBackOff"), creation_offset=10)]
    session = make_session(pods=pods)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == []


@pytest.mark.parametrize("reason", ["ContainerCreating", "PodInitializing"])
@pytest.mark.parametrize("age", [600, 36000])
def test_pending_without_fail_fast_reason_never_swept(reason, age):
    pods = [fake_pod("legit", phase="Pending", container_statuses=waiting(reason), creation_offset=age)]
    session = make_session(pods=pods)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == []


def test_pending_with_no_container_statuses_never_swept():
    pods = [fake_pod("legit", phase="Pending", container_statuses=[], creation_offset=36000)]
    session = make_session(pods=pods)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == []


def test_running_crash_loop_backoff_never_swept():
    pods = [
        fake_pod("crashlooping", phase="Running", container_statuses=waiting("CrashLoopBackOff"), creation_offset=36000)
    ]
    session = make_session(pods=pods)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == []


def test_stuck_pod_age_basis_is_creation_timestamp_not_terminal_cascade():
    pods = [fake_pod("stuck", phase="Pending", container_statuses=waiting("ImagePullBackOff"), creation_offset=600)]
    session = make_session(pods=pods)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == [("pod", "stuck")]


def test_job_owned_stuck_pod_not_swept_directly_but_parent_job_is():
    pods = [
        fake_pod(
            "child-pod", phase="Pending", job_name="stuck-job",
            container_statuses=waiting("ImagePullBackOff"), creation_offset=600,
        )
    ]
    jobs = [fake_job("stuck-job", conditions=[])]  # backoffLimit: 0 -> never gets a Failed condition
    session = make_session(pods=pods, jobs=jobs)
    result = orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW)
    assert result == [("job", "stuck-job")]


# --- age boundary ---


@pytest.mark.parametrize("offset,expected", [(299, False), (300, True), (301, True)])
def test_age_boundary_terminal(offset, expected):
    pods = [fake_pod("p", phase="Succeeded", container_statuses=finished(offset))]
    session = make_session(pods=pods)
    swept = bool(orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW))
    assert swept is expected


@pytest.mark.parametrize("offset,expected", [(299, False), (300, True), (301, True)])
def test_age_boundary_stuck(offset, expected):
    pods = [fake_pod("p", phase="Pending", container_statuses=waiting("ImagePullBackOff"), creation_offset=offset)]
    session = make_session(pods=pods)
    swept = bool(orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW))
    assert swept is expected


# --- jobs ---


def test_job_terminal_by_complete_condition_swept():
    jobs = [fake_job("j", conditions=[condition("Complete", last_transition_time=NOW - timedelta(seconds=600))])]
    session = make_session(jobs=jobs)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == [("job", "j")]


def test_job_terminal_by_failed_condition_swept():
    jobs = [fake_job("j", conditions=[condition("Failed", last_transition_time=NOW - timedelta(seconds=600))])]
    session = make_session(jobs=jobs)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == [("job", "j")]


def test_job_with_no_conditions_not_swept():
    jobs = [fake_job("j", conditions=[])]
    session = make_session(jobs=jobs)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == []


def test_job_with_success_criteria_met_but_not_complete_not_swept():
    old = NOW - timedelta(seconds=600)
    jobs = [fake_job("j", conditions=[condition("SuccessCriteriaMet", last_transition_time=old)])]
    session = make_session(jobs=jobs)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 300, now=NOW) == []


# --- self-exclusion ---


def test_self_exclusion_by_run_id_terminal():
    pods = [fake_pod("mine", phase="Succeeded", run_id="current", container_statuses=finished(0))]
    session = make_session(pods=pods)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 0, exclude_run_id="current", now=NOW) == []


def test_self_exclusion_by_run_id_stuck():
    pods = [
        fake_pod(
            "mine", phase="Pending", run_id="current", container_statuses=waiting("ImagePullBackOff"), creation_offset=0
        )
    ]
    session = make_session(pods=pods)
    assert orphans.find_orphaned_resources(session, CREATED_BY, 0, exclude_run_id="current", now=NOW) == []


# --- delete calls / 404 tolerance / partial failure ---


def test_sweep_deletes_pod_and_job_with_correct_calls():
    pods = [fake_pod("p", phase="Succeeded", container_statuses=finished(600))]
    jobs = [fake_job("j", conditions=[condition("Complete", last_transition_time=NOW - timedelta(seconds=600))])]
    session = make_session(pods=pods, jobs=jobs)
    count = orphans.sweep_orphans(session, basic_spec(), now=NOW)
    assert count == 2
    session.core.delete_namespaced_pod.assert_called_once_with("p", "default")
    args, kwargs = session.batch.delete_namespaced_job.call_args
    assert args == ("j", "default")
    assert kwargs["body"].propagation_policy == "Background"


def test_stuck_pod_deletes_via_identical_call_as_terminal_pod():
    pods = [fake_pod("stuck", phase="Pending", container_statuses=waiting("ImagePullBackOff"), creation_offset=600)]
    session = make_session(pods=pods)
    orphans.sweep_orphans(session, basic_spec(), now=NOW)
    session.core.delete_namespaced_pod.assert_called_once_with("stuck", "default")


def test_delete_404_tolerated_and_not_counted():
    pods = [fake_pod("p", phase="Succeeded", container_statuses=finished(600))]
    session = make_session(pods=pods)
    session.core.delete_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")
    assert orphans.sweep_orphans(session, basic_spec(), now=NOW) == 0


def test_one_delete_failure_does_not_abort_the_rest():
    pods = [
        fake_pod("p1", phase="Succeeded", container_statuses=finished(600)),
        fake_pod("p2", phase="Succeeded", container_statuses=finished(600)),
    ]
    session = make_session(pods=pods)
    session.core.delete_namespaced_pod.side_effect = [
        ApiException(status=500, reason="Server Error"),
        None,
    ]
    assert orphans.sweep_orphans(session, basic_spec(), now=NOW) == 1


# --- output ---


def test_summary_message_on_nonzero_count(capsys):
    pods = [fake_pod("p", phase="Succeeded", container_statuses=finished(600))]
    session = make_session(pods=pods)
    orphans.sweep_orphans(session, basic_spec(quiet=False), now=NOW)
    assert "swept 1 orphaned resource" in capsys.readouterr().err


def test_no_message_when_nothing_swept(capsys):
    session = make_session()
    orphans.sweep_orphans(session, basic_spec(quiet=False), now=NOW)
    assert capsys.readouterr().err == ""


def test_quiet_suppresses_summary_message(capsys):
    pods = [fake_pod("p", phase="Succeeded", container_statuses=finished(600))]
    session = make_session(pods=pods)
    orphans.sweep_orphans(session, basic_spec(quiet=True), now=NOW)
    assert capsys.readouterr().err == ""


def test_list_failure_raises_apierror_naming_the_opt_out():
    session = make_session()
    session.core.list_namespaced_pod.side_effect = ApiException(status=403, reason="Forbidden")
    with pytest.raises(ApiError, match="orphan_sweep"):
        orphans.sweep_orphans(session, basic_spec(), now=NOW)

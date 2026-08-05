from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from squarepeg.k8s import podstate
from squarepeg.k8s import runner as runner_module

NOW = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def pod(phase="Running", container_statuses=None, creation_offset=None):
    creation = NOW - timedelta(seconds=creation_offset) if creation_offset is not None else None
    return SimpleNamespace(
        status=SimpleNamespace(phase=phase, container_statuses=container_statuses or []),
        metadata=SimpleNamespace(creation_timestamp=creation),
    )


def container_status(name="main", waiting_reason=None, finished_at=None):
    waiting = SimpleNamespace(reason=waiting_reason) if waiting_reason else None
    terminated = SimpleNamespace(finished_at=finished_at) if finished_at is not None else None
    return SimpleNamespace(name=name, state=SimpleNamespace(waiting=waiting, terminated=terminated))


def job(conditions=None):
    return SimpleNamespace(status=SimpleNamespace(conditions=conditions or []))


def condition(type_, status="True", last_transition_time=None):
    return SimpleNamespace(type=type_, status=status, last_transition_time=last_transition_time)


def test_fail_fast_reasons_is_the_same_object_runner_uses():
    """Pins the single-source-of-truth property: a future addition to the fail-fast set
    must apply to both runner.py's fail-fast check and the orphan sweep automatically."""
    assert runner_module.FAIL_FAST_REASONS is podstate.FAIL_FAST_REASONS


@pytest.mark.parametrize("reason", sorted(podstate.FAIL_FAST_REASONS))
def test_stuck_waiting_reason_detects_every_fail_fast_reason(reason):
    p = pod(container_statuses=[container_status(waiting_reason=reason)])
    assert podstate.stuck_waiting_reason(p) == reason


@pytest.mark.parametrize("reason", ["ContainerCreating", "PodInitializing"])
def test_stuck_waiting_reason_none_for_benign_reasons(reason):
    p = pod(container_statuses=[container_status(waiting_reason=reason)])
    assert podstate.stuck_waiting_reason(p) is None


def test_stuck_waiting_reason_none_with_no_container_statuses():
    assert podstate.stuck_waiting_reason(pod(container_statuses=[])) is None


def test_stuck_waiting_reason_found_on_sidecar_not_just_first_container():
    p = pod(
        container_statuses=[
            container_status(name="main", waiting_reason="ContainerCreating"),
            container_status(name="sidecar", waiting_reason="ImagePullBackOff"),
        ]
    )
    assert podstate.stuck_waiting_reason(p) == "ImagePullBackOff"


def test_terminal_age_seconds_uses_max_finished_at_across_containers():
    p = pod(
        container_statuses=[
            container_status(name="sidecar", finished_at=NOW - timedelta(seconds=600)),
            container_status(name="main", finished_at=NOW - timedelta(seconds=10)),
        ]
    )
    assert podstate.terminal_age_seconds(p, NOW) == pytest.approx(10)


def test_terminal_age_seconds_uses_max_of_partial_finished_at():
    p = pod(
        container_statuses=[
            container_status(name="sidecar"),  # no terminated info at all
            container_status(name="main", finished_at=NOW - timedelta(seconds=30)),
        ]
    )
    assert podstate.terminal_age_seconds(p, NOW) == pytest.approx(30)


def test_terminal_age_seconds_falls_back_to_creation_timestamp():
    p = pod(container_statuses=[container_status(name="main")], creation_offset=120)
    assert podstate.terminal_age_seconds(p, NOW) == pytest.approx(120)


def test_pod_age_seconds_uses_creation_timestamp():
    p = pod(creation_offset=300)
    assert podstate.pod_age_seconds(p, NOW) == pytest.approx(300)


def test_age_helpers_return_none_when_no_timestamp_at_all():
    p = pod(container_statuses=[], creation_offset=None)
    assert podstate.terminal_age_seconds(p, NOW) is None
    assert podstate.pod_age_seconds(p, NOW) is None


def test_age_clamps_future_timestamp_to_zero():
    p = pod(creation_offset=-60)  # "created" 60s in the future: clock skew
    assert podstate.pod_age_seconds(p, NOW) == 0.0


def test_age_handles_naive_datetime_without_raising():
    naive = NOW.replace(tzinfo=None) - timedelta(seconds=42)
    p = pod(container_statuses=[container_status(finished_at=naive)])
    assert podstate.terminal_age_seconds(p, NOW) == pytest.approx(42)


def test_job_terminal_condition_matches_complete():
    j = job([condition("Complete")])
    assert podstate.job_terminal_condition(j).type == "Complete"


def test_job_terminal_condition_matches_failed():
    j = job([condition("Failed")])
    assert podstate.job_terminal_condition(j).type == "Failed"


def test_job_terminal_condition_none_when_no_conditions():
    assert podstate.job_terminal_condition(job([])) is None


def test_job_terminal_condition_ignores_non_true_status():
    j = job([condition("Complete", status="False")])
    assert podstate.job_terminal_condition(j) is None


def test_job_terminal_condition_ignores_other_condition_types():
    j = job([condition("SuccessCriteriaMet")])
    assert podstate.job_terminal_condition(j) is None


def test_job_terminal_age_seconds_uses_condition_transition_time():
    j = job([condition("Complete", last_transition_time=NOW - timedelta(seconds=500))])
    assert podstate.job_terminal_age_seconds(j, NOW) == pytest.approx(500)


def test_job_terminal_age_seconds_none_when_not_terminal():
    assert podstate.job_terminal_age_seconds(job([]), NOW) is None

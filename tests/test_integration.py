"""Integration tests against a real cluster. Gated by conftest.k8s_cluster:
requires the pytest marker (skipped by default via addopts), SQUAREPEG_INTEGRATION=1,
and an actual reachable cluster. Run with: SQUAREPEG_INTEGRATION=1 pytest -m integration
"""

import time

import pytest
import yaml
from kubernetes import client

pytestmark = pytest.mark.integration

IMAGE = "busybox:1.36"


def test_success_exit_code(run_cli):
    result = run_cli(IMAGE, "true")
    assert result.returncode == 0, result.stderr


def test_nonzero_exit_code(run_cli):
    result = run_cli(IMAGE, "sh", "-c", "exit 7")
    assert result.returncode == 7, result.stderr


def test_stdout_matches_byte_for_byte(run_cli):
    result = run_cli(IMAGE, "sh", "-c", "echo one; echo two; echo three")
    assert result.stdout.splitlines() == ["one", "two", "three"]


def test_env_var_is_set(run_cli):
    result = run_cli("-e", "FOO=bar", IMAGE, "sh", "-c", "echo $FOO")
    assert result.stdout.strip() == "bar"


def test_workdir_is_honoured(run_cli):
    result = run_cli("-w", "/tmp", IMAGE, "pwd")
    assert result.stdout.strip() == "/tmp"


def test_volume_write_then_read_back(run_cli):
    result = run_cli("-v", "/scratch", IMAGE, "sh", "-c", "echo hello > /scratch/f && cat /scratch/f")
    assert result.stdout.strip() == "hello"


def test_job_mode_success_and_failure(run_cli):
    ok = run_cli("--mode", "job", IMAGE, "true")
    assert ok.returncode == 0, ok.stderr
    bad = run_cli("--mode", "job", IMAGE, "sh", "-c", "exit 3")
    assert bad.returncode == 3, bad.stderr


def test_cleanup_removes_pod_after_run(run_cli, test_namespace):
    result = run_cli("--name", "squarepeg-it-cleanup-check", IMAGE, "true")
    assert result.returncode == 0, result.stderr
    core = client.CoreV1Api()
    with pytest.raises(client.rest.ApiException) as exc_info:
        core.read_namespaced_pod("squarepeg-it-cleanup-check", test_namespace)
    assert exc_info.value.status == 404


def test_keep_retains_pod(run_cli, test_namespace):
    name = "squarepeg-it-keep-check"
    result = run_cli("--name", name, "--keep", IMAGE, "true")
    assert result.returncode == 0, result.stderr
    core = client.CoreV1Api()
    try:
        pod = core.read_namespaced_pod(name, test_namespace)
        assert pod.status.phase == "Succeeded"
    finally:
        core.delete_namespaced_pod(name, test_namespace)


def test_dry_run_creates_nothing(run_cli, test_namespace):
    result = run_cli("--dry-run", "--name", "squarepeg-it-dry-run-check", IMAGE, "true")
    assert result.returncode == 0, result.stderr
    manifest = yaml.safe_load(result.stdout)
    assert manifest["kind"] == "Pod"

    core = client.CoreV1Api()
    with pytest.raises(client.rest.ApiException) as exc_info:
        core.read_namespaced_pod("squarepeg-it-dry-run-check", test_namespace)
    assert exc_info.value.status == 404


def test_image_pull_failure_fails_fast(run_cli):
    start = time.monotonic()
    result = run_cli("--timeout", "60", "nosuchregistry.invalid/nope:1", "true")
    elapsed = time.monotonic() - start
    assert result.returncode == 125
    assert elapsed < 60  # must fail fast, not sit out the full startup timeout


def test_passthrough_labels_land_on_live_pod(run_cli, test_namespace, tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("kubernetes:\n  metadata:\n    labels: {team: squarepeg-it}\n")
    name = "squarepeg-it-passthrough-check"
    result = run_cli("--config", str(config_file), "--keep", "--name", name, IMAGE, "true")
    assert result.returncode == 0, result.stderr
    core = client.CoreV1Api()
    try:
        pod = core.read_namespaced_pod(name, test_namespace)
        assert pod.metadata.labels.get("team") == "squarepeg-it"
    finally:
        core.delete_namespaced_pod(name, test_namespace)


def test_incremental_streaming_not_buffered_until_exit(run_cli):
    """Guards against reintroducing _preload_content=True, which would buffer everything
    until the container exits instead of streaming as it runs."""
    result = run_cli(IMAGE, "sh", "-c", "echo first; sleep 3; echo second")
    assert result.stdout.splitlines() == ["first", "second"]

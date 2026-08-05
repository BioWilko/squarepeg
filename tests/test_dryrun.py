import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import yaml
from kubernetes import client
from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError
from squarepeg.k8s import dryrun
from squarepeg.runspec import RunSpec


def make_session():
    session = SimpleNamespace()
    session.core = MagicMock()
    session.batch = MagicMock()
    session.namespace = "default"
    return session


def basic_spec(**overrides):
    defaults = dict(image="alpine", name="squarepeg-alpine-abc123")
    defaults.update(overrides)
    return RunSpec(**defaults)


def _api_exception_with_body(status, message):
    exc = ApiException(status=status, reason="reason")
    exc.body = json.dumps({"message": message})
    return exc


# --- call shape ---


def test_pod_mode_calls_create_namespaced_pod_with_dry_run():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    session.core.create_namespaced_pod.return_value = manifest
    dryrun.server_dry_run(session, basic_spec(mode="pod"), manifest)
    session.core.create_namespaced_pod.assert_called_once_with("default", manifest, dry_run=dryrun.SERVER_DRY_RUN)
    session.batch.create_namespaced_job.assert_not_called()


def test_job_mode_calls_create_namespaced_job_with_dry_run():
    session = make_session()
    manifest = {"metadata": {"name": "j"}}
    session.batch.create_namespaced_job.return_value = manifest
    dryrun.server_dry_run(session, basic_spec(mode="job"), manifest)
    session.batch.create_namespaced_job.assert_called_once_with("default", manifest, dry_run=dryrun.SERVER_DRY_RUN)
    session.core.create_namespaced_pod.assert_not_called()


def test_manifest_object_identity_preserved():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    session.core.create_namespaced_pod.return_value = manifest
    dryrun.server_dry_run(session, basic_spec(mode="pod"), manifest)
    args, _kwargs = session.core.create_namespaced_pod.call_args
    assert args[1] is manifest


# --- response serialization ---


def test_typed_response_serialized_to_plain_dict():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    pod = client.V1Pod(
        api_version="v1",
        kind="Pod",
        metadata=client.V1ObjectMeta(name="p", namespace="default"),
    )
    session.core.create_namespaced_pod.return_value = pod
    result = dryrun.server_dry_run(session, basic_spec(mode="pod"), manifest)
    assert isinstance(result, dict)
    assert result["apiVersion"] == "v1"
    assert result["kind"] == "Pod"
    assert result["metadata"]["name"] == "p"
    assert "status" not in result  # None fields are dropped by sanitize_for_serialization
    yaml.safe_dump(result)  # must not raise -- no Python objects survive


def test_dict_response_passes_through_unchanged():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    fake_response = {"already": "plain"}
    session.core.create_namespaced_pod.return_value = fake_response
    result = dryrun.server_dry_run(session, basic_spec(mode="pod"), manifest)
    assert result is fake_response


# --- error handling reuse ---


def test_422_rejection_wraps_with_hint():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    session.core.create_namespaced_pod.side_effect = _api_exception_with_body(
        422, "Pod \"p\" is invalid: spec.nodeSelector: Invalid value"
    )
    try:
        dryrun.server_dry_run(session, basic_spec(mode="pod"), manifest)
        assert False, "expected ApiError"
    except ApiError as exc:
        message = str(exc)
        assert "validate" in message
        assert "against the apiserver" in message
        assert "nodeSelector" in message
        assert "--dry-run" in message


def test_403_quota_rejection_uses_quota_hint_not_rbac_hint():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    session.core.create_namespaced_pod.side_effect = _api_exception_with_body(
        403, "pods \"p\" is forbidden: failed quota: quota: must specify requests.cpu for: main"
    )
    try:
        dryrun.server_dry_run(session, basic_spec(mode="pod"), manifest)
        assert False, "expected ApiError"
    except ApiError as exc:
        message = str(exc)
        assert "ResourceQuota" in message
        assert "kubectl auth can-i" not in message


def test_409_conflict_uses_already_exists_hint():
    session = make_session()
    manifest = {"metadata": {"name": "p"}}
    session.core.create_namespaced_pod.side_effect = _api_exception_with_body(409, "pods \"p\" already exists")
    try:
        dryrun.server_dry_run(session, basic_spec(mode="pod"), manifest)
        assert False, "expected ApiError"
    except ApiError as exc:
        assert "already exists" in str(exc)

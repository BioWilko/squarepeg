from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError
from squarepeg.k8s.apierrors import delete_resource


def make_session():
    session = SimpleNamespace()
    session.core = MagicMock()
    session.batch = MagicMock()
    session.namespace = "default"
    return session


def test_delete_resource_pod_calls_delete_namespaced_pod():
    session = make_session()
    assert delete_resource(session, "pod", "p") is True
    session.core.delete_namespaced_pod.assert_called_once_with("p", "default")


def test_delete_resource_job_uses_background_propagation():
    session = make_session()
    assert delete_resource(session, "job", "j") is True
    args, kwargs = session.batch.delete_namespaced_job.call_args
    assert args == ("j", "default")
    assert kwargs["body"].propagation_policy == "Background"


def test_delete_resource_returns_false_on_404():
    session = make_session()
    session.core.delete_namespaced_pod.side_effect = ApiException(status=404, reason="Not Found")
    assert delete_resource(session, "pod", "p") is False


def test_delete_resource_raises_on_other_errors():
    session = make_session()
    session.core.delete_namespaced_pod.side_effect = ApiException(status=500, reason="Server Error")
    with pytest.raises(ApiError):
        delete_resource(session, "pod", "p")

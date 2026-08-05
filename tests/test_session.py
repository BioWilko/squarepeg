from unittest.mock import MagicMock

import pytest
from kubernetes.config.config_exception import ConfigException

from squarepeg.errors import RunnerError
from squarepeg.k8s import session as session_module


def _ctx(name="ctx", namespace=None):
    entry = {"name": name, "context": ({"namespace": namespace} if namespace else {})}
    return [entry], entry


def test_load_failure_raises_runner_error_when_incluster_also_fails(monkeypatch):
    monkeypatch.setattr(
        session_module.kube_config, "load_kube_config", MagicMock(side_effect=ConfigException("no kubeconfig"))
    )
    monkeypatch.setattr(
        session_module.kube_config, "load_incluster_config", MagicMock(side_effect=ConfigException("not in a pod"))
    )
    with pytest.raises(RunnerError, match="kubeconfig"):
        session_module.Session()


def test_namespace_falls_back_to_context_namespace(monkeypatch):
    monkeypatch.setattr(session_module.kube_config, "load_kube_config", MagicMock())
    monkeypatch.setattr(
        session_module.kube_config,
        "list_kube_config_contexts",
        MagicMock(return_value=_ctx(namespace="from-context")),
    )
    s = session_module.Session(quiet=True)
    assert s.namespace == "from-context"
    assert s.context_name == "ctx"


def test_explicit_namespace_wins_over_context(monkeypatch):
    monkeypatch.setattr(session_module.kube_config, "load_kube_config", MagicMock())
    monkeypatch.setattr(
        session_module.kube_config,
        "list_kube_config_contexts",
        MagicMock(return_value=_ctx(namespace="from-context")),
    )
    s = session_module.Session(namespace="explicit", quiet=True)
    assert s.namespace == "explicit"


def test_namespace_defaults_to_default_when_nothing_resolves(monkeypatch):
    monkeypatch.setattr(session_module.kube_config, "load_kube_config", MagicMock())
    monkeypatch.setattr(
        session_module.kube_config, "list_kube_config_contexts", MagicMock(side_effect=ConfigException("no contexts"))
    )
    s = session_module.Session(quiet=True)
    assert s.namespace == "default"


def test_unknown_context_raises(monkeypatch):
    monkeypatch.setattr(session_module.kube_config, "load_kube_config", MagicMock())
    monkeypatch.setattr(session_module.kube_config, "list_kube_config_contexts", MagicMock(return_value=_ctx()))
    with pytest.raises(RunnerError, match="no such kubeconfig context"):
        session_module.Session(context="nonexistent", quiet=True)


# --- in-cluster fallback ---


def test_falls_back_to_incluster_config_when_no_kubeconfig(monkeypatch, tmp_path):
    monkeypatch.setattr(
        session_module.kube_config, "load_kube_config", MagicMock(side_effect=ConfigException("no kubeconfig"))
    )
    monkeypatch.setattr(session_module.kube_config, "load_incluster_config", MagicMock())
    ns_file = tmp_path / "namespace"
    ns_file.write_text("my-incluster-ns\n")
    monkeypatch.setattr(session_module, "_INCLUSTER_NAMESPACE_PATH", ns_file)

    s = session_module.Session(quiet=True)

    assert s.context_name == session_module._IN_CLUSTER_CONTEXT_NAME
    assert s.namespace == "my-incluster-ns"


def test_incluster_fallback_defaults_to_default_namespace_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(
        session_module.kube_config, "load_kube_config", MagicMock(side_effect=ConfigException("no kubeconfig"))
    )
    monkeypatch.setattr(session_module.kube_config, "load_incluster_config", MagicMock())
    monkeypatch.setattr(session_module, "_INCLUSTER_NAMESPACE_PATH", tmp_path / "does-not-exist")

    s = session_module.Session(quiet=True)

    assert s.namespace == "default"


def test_explicit_namespace_wins_over_incluster_namespace_file(monkeypatch, tmp_path):
    monkeypatch.setattr(
        session_module.kube_config, "load_kube_config", MagicMock(side_effect=ConfigException("no kubeconfig"))
    )
    monkeypatch.setattr(session_module.kube_config, "load_incluster_config", MagicMock())
    ns_file = tmp_path / "namespace"
    ns_file.write_text("my-incluster-ns\n")
    monkeypatch.setattr(session_module, "_INCLUSTER_NAMESPACE_PATH", ns_file)

    s = session_module.Session(namespace="explicit", quiet=True)

    assert s.namespace == "explicit"


def test_explicit_context_failure_does_not_fall_back_to_incluster(monkeypatch):
    """An explicit --context that fails to load must surface that failure, not be silently
    masked by an in-cluster fallback the user never asked for."""
    monkeypatch.setattr(
        session_module.kube_config, "load_kube_config", MagicMock(side_effect=ConfigException("no such context"))
    )
    incluster = MagicMock()
    monkeypatch.setattr(session_module.kube_config, "load_incluster_config", incluster)

    with pytest.raises(RunnerError, match="context 'myctx'"):
        session_module.Session(context="myctx", quiet=True)

    incluster.assert_not_called()

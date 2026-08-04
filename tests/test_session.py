from unittest.mock import MagicMock

import pytest
from kubernetes.config.config_exception import ConfigException

from squarepeg.errors import RunnerError
from squarepeg.k8s import session as session_module


def _ctx(name="ctx", namespace=None):
    entry = {"name": name, "context": ({"namespace": namespace} if namespace else {})}
    return [entry], entry


def test_load_failure_raises_runner_error(monkeypatch):
    load = MagicMock(side_effect=ConfigException("no kubeconfig"))
    monkeypatch.setattr(session_module.kube_config, "load_kube_config", load)
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

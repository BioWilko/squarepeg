import os
import subprocess
import sys
import uuid

import pytest

INTEGRATION_ENV_VAR = "SQUAREPEG_INTEGRATION"


@pytest.fixture(scope="session")
def k8s_cluster():
    """Guard integration tests behind an explicit opt-in and a reachable cluster.

    Three independent guards, deliberately: the pytest marker (skipped by default via
    addopts), this env var opt-in, and an actual connectivity check -- an accidental run
    against a real cluster is a real hazard in a TRE context.
    """
    if os.environ.get(INTEGRATION_ENV_VAR) != "1":
        pytest.skip(f"set {INTEGRATION_ENV_VAR}=1 to run integration tests against a real cluster")

    from kubernetes import client, config
    from kubernetes.config.config_exception import ConfigException

    try:
        config.load_kube_config()
    except (ConfigException, FileNotFoundError) as exc:
        pytest.skip(f"no kubeconfig available: {exc}")

    try:
        _contexts, active_context = config.list_kube_config_contexts()
    except ConfigException:
        active_context = None
    context_name = (active_context or {}).get("name", "<unknown>")

    try:
        client.VersionApi().get_code(_request_timeout=5)
    except Exception as exc:
        pytest.skip(f"cluster unreachable for context {context_name!r}: {exc}")

    print(f"\n[integration] using kubeconfig context {context_name!r}", file=sys.stderr)
    return {"context": context_name}


@pytest.fixture(scope="session")
def test_namespace(k8s_cluster):
    from kubernetes import client

    core = client.CoreV1Api()
    name = f"squarepeg-it-{uuid.uuid4().hex[:8]}"
    core.create_namespace(client.V1Namespace(metadata=client.V1ObjectMeta(name=name)))
    try:
        yield name
    finally:
        core.delete_namespace(name)


@pytest.fixture
def run_cli(test_namespace, tmp_path):
    """Invoke the installed console script as a subprocess, isolated from any local config."""

    def _run(*args, timeout=120, env_overrides=None):
        env = dict(os.environ)
        env["SQUAREPEG_CONFIG"] = ""
        cmd = [sys.executable, "-m", "squarepeg", "run", "--no-default-config", "-n", test_namespace, *args]
        if env_overrides:
            env.update(env_overrides)
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=env)

    return _run

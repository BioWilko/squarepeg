from pathlib import Path

from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.config.config_exception import ConfigException

from squarepeg.errors import RunnerError
from squarepeg.log import chatter

# where the in-cluster service account's namespace is projected, if squarepeg
# itself is running as a pod (e.g. inside a CI/CD pipeline that spawns sibling
# pods/jobs using its own pod's service account)
_INCLUSTER_NAMESPACE_PATH = Path("/var/run/secrets/kubernetes.io/serviceaccount/namespace")
_IN_CLUSTER_CONTEXT_NAME = "in-cluster"


def _resolve_context_namespace(context: str | None) -> tuple[str | None, str | None]:
    try:
        contexts, active_context = kube_config.list_kube_config_contexts()
    except ConfigException:
        return None, None
    chosen = active_context
    if context is not None:
        chosen = next((c for c in contexts if c["name"] == context), None)
        if chosen is None:
            raise RunnerError(f"no such kubeconfig context {context!r}")
    if chosen is None:
        return None, None
    return chosen["name"], (chosen.get("context") or {}).get("namespace")


def _read_incluster_namespace() -> str | None:
    try:
        return _INCLUSTER_NAMESPACE_PATH.read_text().strip()
    except OSError:
        return None


class Session:
    """Holds authenticated Kubernetes API clients plus the resolved namespace.

    Tries a kubeconfig first (kubectl's own resolution: $KUBECONFIG, then
    ~/.kube/config, honouring --context) so the common laptop-against-a-remote-
    cluster case is unchanged. Only falls back to in-cluster config (the pod's
    own service account) when no --context was explicitly requested and no
    kubeconfig could be loaded at all -- an explicit --context that fails should
    surface that failure, not be silently masked by an in-cluster fallback.
    """

    def __init__(self, namespace: str | None = None, context: str | None = None, quiet: bool = False):
        in_cluster = False
        try:
            kube_config.load_kube_config(context=context)
        except (ConfigException, FileNotFoundError) as exc:
            if context is not None:
                raise RunnerError(
                    f"could not load kubeconfig context {context!r}: {exc}. Check $KUBECONFIG and ~/.kube/config."
                ) from exc
            try:
                kube_config.load_incluster_config()
                in_cluster = True
            except ConfigException as incluster_exc:
                raise RunnerError(
                    f"could not load a kubeconfig ({exc}) and doesn't look like it's running in-cluster "
                    f"({incluster_exc}). Check $KUBECONFIG and ~/.kube/config."
                ) from incluster_exc

        if in_cluster:
            self.context_name = _IN_CLUSTER_CONTEXT_NAME
            context_namespace = _read_incluster_namespace()
        else:
            self.context_name, context_namespace = _resolve_context_namespace(context)

        self.namespace = namespace or context_namespace or "default"

        self.core = client.CoreV1Api()
        self.batch = client.BatchV1Api()

        chatter(f"using context {self.context_name!r}, namespace {self.namespace!r}", quiet=quiet)

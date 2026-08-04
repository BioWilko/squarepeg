from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.config.config_exception import ConfigException

from squarepeg.errors import RunnerError
from squarepeg.log import chatter


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


class Session:
    """Holds authenticated Kubernetes API clients plus the resolved namespace."""

    def __init__(self, namespace: str | None = None, context: str | None = None, quiet: bool = False):
        try:
            kube_config.load_kube_config(context=context)
        except (ConfigException, FileNotFoundError) as exc:
            raise RunnerError(
                f"could not load a kubeconfig (context={context!r}): {exc}. "
                "Check $KUBECONFIG and ~/.kube/config."
            ) from exc

        context_name, context_namespace = _resolve_context_namespace(context)
        self.context_name = context_name
        self.namespace = namespace or context_namespace or "default"

        self.core = client.CoreV1Api()
        self.batch = client.BatchV1Api()

        chatter(f"using context {self.context_name!r}, namespace {self.namespace!r}", quiet=quiet)

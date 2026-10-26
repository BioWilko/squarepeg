import ssl
from pathlib import Path

from kubernetes import client
from kubernetes import config as kube_config
from kubernetes.config.config_exception import ConfigException
from urllib3.util.ssl_ import create_urllib3_context

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

    def __init__(
        self,
        namespace: str | None = None,
        context: str | None = None,
        quiet: bool = False,
        lenient_tls: bool = False,
    ):
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

        api_client = None
        if lenient_tls:
            # short-term workaround for https://github.com/canonical/microk8s/issues/4864: microk8s's CA
            # lacks the keyUsage extension, which Python 3.13+/urllib3 2.3+ reject via VERIFY_X509_STRICT.
            # Only that flag is cleared -- CERT_REQUIRED, CA chain and hostname checks are all still
            # enforced. The kubernetes client exposes no ssl_context option, so it's injected into the
            # (lazily created) urllib3 connection pools' kwargs instead.
            ssl_context = create_urllib3_context()
            ssl_context.verify_flags &= ~ssl.VERIFY_X509_STRICT
            api_client = client.ApiClient()
            api_client.rest_client.pool_manager.connection_pool_kw["ssl_context"] = ssl_context

        self.core = client.CoreV1Api(api_client)
        self.batch = client.BatchV1Api(api_client)

        chatter(f"using context {self.context_name!r}, namespace {self.namespace!r}", quiet=quiet)

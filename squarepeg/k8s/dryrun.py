"""Submit a manifest to the apiserver as a server-side dry run (nothing is persisted).

Deliberately separate from runner.py: that module's concern is the full create/stream/wait/
cleanup lifecycle of a resource that actually exists. Server-side dry-run shares none of
that -- it's a single validate-and-discard apiserver call.
"""

from kubernetes import client
from kubernetes.client.rest import ApiException

from squarepeg.k8s.apierrors import wrap_api_exception
from squarepeg.runspec import RunSpec

SERVER_DRY_RUN = "All"  # the only value the Kubernetes API accepts

_SERIALIZER: client.ApiClient | None = None


def _api_client() -> client.ApiClient:
    global _SERIALIZER
    if _SERIALIZER is None:
        _SERIALIZER = client.ApiClient()
    return _SERIALIZER


def _to_plain_dict(response):
    if isinstance(response, dict):
        return response
    return _api_client().sanitize_for_serialization(response)


def server_dry_run(session, spec: RunSpec, manifest: dict) -> dict:
    """Submit `manifest` to the apiserver with dryRun=All and return its response as a plain dict.

    Runs full validation, defaulting, and admission/mutating webhooks; nothing is persisted.
    Raises ApiError (via wrap_api_exception) on rejection, reusing the same hints as a real
    create failure.
    """
    name = manifest["metadata"]["name"]
    try:
        if spec.mode == "job":
            response = session.batch.create_namespaced_job(session.namespace, manifest, dry_run=SERVER_DRY_RUN)
        else:
            response = session.core.create_namespaced_pod(session.namespace, manifest, dry_run=SERVER_DRY_RUN)
    except ApiException as exc:
        raise wrap_api_exception(exc, f"failed to validate {spec.mode} {name!r} against the apiserver") from exc
    return _to_plain_dict(response)

"""Shared Kubernetes API-error interpretation and the delete primitive.

Split out of runner.py so both the per-run cleanup path and the orphan sweep
(squarepeg/k8s/orphans.py) can share the exact same error wrapping and 404
tolerance, rather than risking two copies drifting apart.
"""

import json

from kubernetes import client
from kubernetes.client.rest import ApiException

from squarepeg.errors import ApiError

API_ERROR_HINTS = {
    400: "the apiserver rejected the manifest as malformed; run with --dry-run to inspect it",
    401: "authentication failed; try 'kubectl get pods' to check your credentials",
    403: "permission denied; check with 'kubectl auth can-i create pods'",
    404: "not found; check the namespace exists",
    409: "already exists; omit --name, or clean up a --keep'd previous run",
    422: "the apiserver rejected the manifest; run with --dry-run to inspect it",
}

# A ResourceQuota admission rejection also arrives as HTTP 403, but it has nothing to do with
# RBAC permissions -- the generic 403 hint above ("check with kubectl auth can-i") is actively
# misleading here. Detected by the admission plugin's standard "failed quota: ..." message
# text, which names the missing requests/limits per container.
QUOTA_ERROR_MARKER = "failed quota"
QUOTA_HINT = (
    "the target namespace enforces a ResourceQuota requiring cpu/memory requests (and "
    "possibly limits) on every container; set --cpus/--memory (or --request-cpu/--request-"
    "memory/--limit-cpu/--limit-memory), or 'defaults.cpus'/'defaults.memory' in config so "
    "you don't have to pass them on every run"
)


def api_exception_detail(exc: ApiException) -> str:
    """Extract the Kubernetes Status object's 'message' field from the response body, if
    present -- this is almost always far more specific than exc.reason (the bare HTTP
    reason phrase, e.g. 'Bad Request'), naming the exact field the apiserver rejected."""
    if exc.body:
        try:
            body = json.loads(exc.body)
        except (TypeError, ValueError):
            body = None
        if isinstance(body, dict) and body.get("message"):
            return body["message"]
    return exc.reason


def wrap_api_exception(exc: ApiException, action: str) -> ApiError:
    detail = api_exception_detail(exc)
    if exc.status == 403 and QUOTA_ERROR_MARKER in detail.lower():
        hint = QUOTA_HINT
    else:
        hint = API_ERROR_HINTS.get(exc.status)
    message = f"{action}: {detail}"
    if hint:
        message += f" ({hint})"
    return ApiError(message)


def delete_resource(session, kind: str, name: str) -> bool:
    """Delete a Pod or Job by name, tolerating 404 (already gone).

    Returns True if the delete actually removed something, False if it was already gone.
    Raises ApiError for any other failure.
    """
    try:
        if kind == "job":
            session.batch.delete_namespaced_job(
                name, session.namespace, body=client.V1DeleteOptions(propagation_policy="Background")
            )
        else:
            session.core.delete_namespaced_pod(name, session.namespace)
    except ApiException as exc:
        if exc.status == 404:
            return False
        raise wrap_api_exception(exc, f"failed to delete {kind} {name!r}") from exc
    return True

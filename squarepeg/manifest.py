"""Build Pod/Job manifests as plain dicts (not typed V1* models), so that
arbitrary k8s passthrough config can be deep-merged in directly and
--dry-run output is exactly what would be sent to the apiserver.
"""

import getpass
import uuid

from squarepeg.merge import deep_merge
from squarepeg.naming import sanitize_label_value
from squarepeg.runspec import RunSpec
from squarepeg.volumes import HostPathMount, VolumeMount

DEFAULT_JOB_TTL_SECONDS_AFTER_FINISHED = 86400


def _managed_labels(run_id: str | None = None) -> dict:
    return {
        "app.kubernetes.io/managed-by": "squarepeg",
        "squarepeg.io/run-id": run_id or str(uuid.uuid4()),
        "squarepeg.io/created-by": sanitize_label_value(getpass.getuser()),
    }


def _volume_source(volume: VolumeMount) -> dict:
    if isinstance(volume, HostPathMount):
        return {"hostPath": {"path": volume.host_path}}
    if volume.source is not None:
        return dict(volume.source)
    return {"emptyDir": {}}


def _build_volumes(spec: RunSpec) -> list[dict]:
    return [{"name": v.volume_name, **_volume_source(v)} for v in spec.volumes]


def _build_volume_mounts(spec: RunSpec) -> list[dict]:
    mounts = []
    for v in spec.volumes:
        mount = {"name": v.volume_name, "mountPath": v.container_path}
        if v.read_only:
            mount["readOnly"] = True
        mounts.append(mount)
    return mounts


def _build_resources(spec: RunSpec) -> dict:
    requests = {}
    limits = {}
    if spec.resources.request_cpu is not None:
        requests["cpu"] = spec.resources.request_cpu
    if spec.resources.request_memory is not None:
        requests["memory"] = spec.resources.request_memory
    if spec.resources.limit_cpu is not None:
        limits["cpu"] = spec.resources.limit_cpu
    if spec.resources.limit_memory is not None:
        limits["memory"] = spec.resources.limit_memory
    resources = {}
    if requests:
        resources["requests"] = requests
    if limits:
        resources["limits"] = limits
    return resources


def _build_container(spec: RunSpec) -> dict:
    container = {"name": spec.container_name, "image": spec.image}
    if spec.entrypoint:
        container["command"] = list(spec.entrypoint)
    if spec.args:
        container["args"] = list(spec.args)
    if spec.workdir:
        container["workingDir"] = spec.workdir
    if spec.env:
        container["env"] = [{"name": k, "value": v} for k, v in spec.env.items()]
    if spec.pull_policy:
        container["imagePullPolicy"] = spec.pull_policy
    if spec.tty:
        container["tty"] = True
    if spec.stdin:
        container["stdin"] = True
    resources = _build_resources(spec)
    if resources:
        container["resources"] = resources
    mounts = _build_volume_mounts(spec)
    if mounts:
        container["volumeMounts"] = mounts
    return container


def _build_pod_spec(spec: RunSpec) -> dict:
    pod_spec = {"restartPolicy": "Never", "containers": [_build_container(spec)]}
    volumes = _build_volumes(spec)
    if volumes:
        pod_spec["volumes"] = volumes
    return pod_spec


def build_pod(spec: RunSpec, kubernetes_passthrough: dict | None = None, run_id: str | None = None) -> dict:
    metadata = {"name": spec.name, "labels": _managed_labels(run_id)}
    if spec.namespace:
        metadata["namespace"] = spec.namespace

    pod = {"apiVersion": "v1", "kind": "Pod", "metadata": metadata, "spec": _build_pod_spec(spec)}

    passthrough = kubernetes_passthrough or {}
    overlay = {}
    if "metadata" in passthrough:
        overlay["metadata"] = passthrough["metadata"]
    if "spec" in passthrough:
        overlay["spec"] = passthrough["spec"]
    return deep_merge(pod, overlay, spec.claims, path="")


def build_job(
    spec: RunSpec,
    kubernetes_passthrough: dict | None = None,
    job_passthrough: dict | None = None,
    run_id: str | None = None,
) -> dict:
    labels = _managed_labels(run_id)
    job_metadata = {"name": spec.name, "labels": dict(labels)}
    if spec.namespace:
        job_metadata["namespace"] = spec.namespace

    template_metadata = {"labels": dict(labels)}
    template_spec = _build_pod_spec(spec)

    kubernetes_passthrough = kubernetes_passthrough or {}
    job_passthrough = job_passthrough or {}

    # kubernetes.metadata/spec in config is always a pod spec/metadata; re-home it
    # under the Job's pod template so config stays portable between --mode pod/job.
    metadata_overlay = kubernetes_passthrough.get("metadata")
    if metadata_overlay:
        job_metadata = deep_merge(job_metadata, metadata_overlay, spec.claims, path="/metadata")
        template_metadata = deep_merge(template_metadata, metadata_overlay, frozenset(), path="")
    spec_overlay = kubernetes_passthrough.get("spec")
    if spec_overlay:
        template_spec = deep_merge(template_spec, spec_overlay, spec.claims, path="/spec")

    job_spec = {
        "backoffLimit": 0,
        "completions": 1,
        "parallelism": 1,
        "ttlSecondsAfterFinished": DEFAULT_JOB_TTL_SECONDS_AFTER_FINISHED,
        "template": {"metadata": template_metadata, "spec": template_spec},
    }
    job_spec_overlay = job_passthrough.get("spec")
    if job_spec_overlay:
        merged = deep_merge({"spec": job_spec}, {"spec": job_spec_overlay}, frozenset(), path="")
        job_spec = merged["spec"]
        job_spec["template"] = {"metadata": template_metadata, "spec": template_spec}

    return {
        "apiVersion": "batch/v1",
        "kind": "Job",
        "metadata": job_metadata,
        "spec": job_spec,
    }

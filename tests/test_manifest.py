import yaml

from squarepeg.manifest import DEFAULT_JOB_TTL_SECONDS_AFTER_FINISHED, build_job, build_pod
from squarepeg.runspec import ResourceSpec, RunSpec
from squarepeg.volumes import VolumeMount

RUN_ID = "11111111-1111-1111-1111-111111111111"


def basic_spec(**overrides) -> RunSpec:
    defaults = dict(image="alpine", args=("true",), name="squarepeg-alpine-abc123")
    defaults.update(overrides)
    return RunSpec(**defaults)


def test_pod_has_restart_policy_never():
    pod = build_pod(basic_spec(), run_id=RUN_ID)
    assert pod["spec"]["restartPolicy"] == "Never"


def test_pod_container_name_and_image():
    pod = build_pod(basic_spec(container_name="main"), run_id=RUN_ID)
    container = pod["spec"]["containers"][0]
    assert container["name"] == "main"
    assert container["image"] == "alpine"
    assert container["args"] == ["true"]


def test_pod_managed_labels_and_run_id():
    pod = build_pod(basic_spec(), run_id=RUN_ID)
    labels = pod["metadata"]["labels"]
    assert labels["app.kubernetes.io/managed-by"] == "squarepeg"
    assert labels["squarepeg.io/run-id"] == RUN_ID
    assert "squarepeg.io/created-by" in labels


def test_pod_default_has_no_keep_label():
    pod = build_pod(basic_spec(cleanup=True), run_id=RUN_ID)
    assert "squarepeg.io/keep" not in pod["metadata"]["labels"]


def test_pod_kept_carries_keep_label():
    pod = build_pod(basic_spec(cleanup=False), run_id=RUN_ID)
    assert pod["metadata"]["labels"]["squarepeg.io/keep"] == "true"


def test_job_default_has_no_keep_label_on_job_or_template():
    j = build_job(basic_spec(cleanup=True), run_id=RUN_ID)
    assert "squarepeg.io/keep" not in j["metadata"]["labels"]
    assert "squarepeg.io/keep" not in j["spec"]["template"]["metadata"]["labels"]


def test_job_kept_carries_keep_label_on_both_job_and_template():
    """The keep label must land on both the Job and its pod template, since the sweep
    queries both kinds and the child pod inherits the identical label set."""
    j = build_job(basic_spec(cleanup=False), run_id=RUN_ID)
    assert j["metadata"]["labels"]["squarepeg.io/keep"] == "true"
    assert j["spec"]["template"]["metadata"]["labels"]["squarepeg.io/keep"] == "true"


def test_pod_workdir_tty_stdin_pull_policy():
    spec = basic_spec(workdir="/work", tty=True, stdin=True, pull_policy="Always")
    pod = build_pod(spec, run_id=RUN_ID)
    container = pod["spec"]["containers"][0]
    assert container["workingDir"] == "/work"
    assert container["tty"] is True
    assert container["stdin"] is True
    assert container["imagePullPolicy"] == "Always"


def test_pod_volumes_paired_with_mounts():
    vol = VolumeMount(volume_name="scratch", container_path="/scratch", read_only=False, source=None)
    pod = build_pod(basic_spec(volumes=[vol]), run_id=RUN_ID)
    assert pod["spec"]["volumes"] == [{"name": "scratch", "emptyDir": {}}]
    assert pod["spec"]["containers"][0]["volumeMounts"] == [{"name": "scratch", "mountPath": "/scratch"}]


def test_pod_same_volume_mounted_twice_produces_one_volume_entry():
    """Regression: a Pod's spec.volumes must have unique names even when the same
    volume is mounted at two different paths via two VolumeMount entries."""
    source = {"persistentVolumeClaim": {"claimName": "shared-pvc"}}
    mounts = [
        VolumeMount(volume_name="shared", container_path="/a", read_only=False, source=source),
        VolumeMount(volume_name="shared", container_path="/b", read_only=True, source=source),
    ]
    pod = build_pod(basic_spec(volumes=mounts), run_id=RUN_ID)
    assert pod["spec"]["volumes"] == [{"name": "shared", "persistentVolumeClaim": {"claimName": "shared-pvc"}}]
    assert pod["spec"]["containers"][0]["volumeMounts"] == [
        {"name": "shared", "mountPath": "/a"},
        {"name": "shared", "mountPath": "/b", "readOnly": True},
    ]


def test_pod_resources_block_from_resource_spec():
    resources = ResourceSpec(request_cpu="500m", limit_cpu="500m", request_memory="512Mi", limit_memory="512Mi")
    pod = build_pod(basic_spec(resources=resources), run_id=RUN_ID)
    assert pod["spec"]["containers"][0]["resources"] == {
        "requests": {"cpu": "500m", "memory": "512Mi"},
        "limits": {"cpu": "500m", "memory": "512Mi"},
    }


def test_pod_no_resources_block_when_unset():
    pod = build_pod(basic_spec(), run_id=RUN_ID)
    assert "resources" not in pod["spec"]["containers"][0]


def test_pod_namespace_only_set_when_given():
    pod = build_pod(basic_spec(), run_id=RUN_ID)
    assert "namespace" not in pod["metadata"]
    pod_ns = build_pod(basic_spec(namespace="myns"), run_id=RUN_ID)
    assert pod_ns["metadata"]["namespace"] == "myns"


def test_pod_passthrough_labels_merge_with_managed_labels():
    passthrough = {"metadata": {"labels": {"team": "bioinf"}}}
    pod = build_pod(basic_spec(), kubernetes_passthrough=passthrough, run_id=RUN_ID)
    labels = pod["metadata"]["labels"]
    assert labels["team"] == "bioinf"
    assert labels["app.kubernetes.io/managed-by"] == "squarepeg"


def test_pod_passthrough_cannot_override_claimed_name():
    spec = basic_spec(claims={"/metadata/name"})
    passthrough = {"metadata": {"name": "hijacked"}}
    pod = build_pod(spec, kubernetes_passthrough=passthrough, run_id=RUN_ID)
    assert pod["metadata"]["name"] == "squarepeg-alpine-abc123"


def test_pod_passthrough_node_selector_and_tolerations_land():
    passthrough = {
        "spec": {
            "nodeSelector": {"disk": "ssd"},
            "tolerations": [{"key": "dedicated", "operator": "Exists"}],
        }
    }
    pod = build_pod(basic_spec(), kubernetes_passthrough=passthrough, run_id=RUN_ID)
    assert pod["spec"]["nodeSelector"] == {"disk": "ssd"}
    assert pod["spec"]["tolerations"] == [{"key": "dedicated", "operator": "Exists"}]


def test_job_has_backoff_limit_zero_and_ttl():
    job = build_job(basic_spec(), run_id=RUN_ID)
    assert job["spec"]["backoffLimit"] == 0
    assert job["spec"]["ttlSecondsAfterFinished"] == DEFAULT_JOB_TTL_SECONDS_AFTER_FINISHED
    assert job["spec"]["template"]["spec"]["restartPolicy"] == "Never"


def test_job_template_labels_include_managed_labels():
    job = build_job(basic_spec(), run_id=RUN_ID)
    assert job["spec"]["template"]["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "squarepeg"
    assert job["metadata"]["labels"]["app.kubernetes.io/managed-by"] == "squarepeg"


def test_job_kubernetes_passthrough_spec_is_a_pod_spec_rehomed():
    passthrough = {"spec": {"nodeSelector": {"disk": "ssd"}, "serviceAccountName": "squarepeg"}}
    job = build_job(basic_spec(), kubernetes_passthrough=passthrough, run_id=RUN_ID)
    template_spec = job["spec"]["template"]["spec"]
    assert template_spec["nodeSelector"] == {"disk": "ssd"}
    assert template_spec["serviceAccountName"] == "squarepeg"
    # never applied at the Job's own top-level spec
    assert "nodeSelector" not in job["spec"]


def test_job_kubernetes_passthrough_respects_claims_in_template():
    spec = basic_spec(claims={"/spec/containers/[name=main]/args"})
    passthrough = {"spec": {"containers": [{"name": "main", "args": ["hijacked"]}]}}
    job = build_job(spec, kubernetes_passthrough=passthrough, run_id=RUN_ID)
    container = job["spec"]["template"]["spec"]["containers"][0]
    assert container["args"] == ["true"]


def test_job_level_passthrough_merges_at_job_root():
    job_passthrough = {"spec": {"backoffLimit": 3, "ttlSecondsAfterFinished": 60}}
    job = build_job(basic_spec(), job_passthrough=job_passthrough, run_id=RUN_ID)
    assert job["spec"]["backoffLimit"] == 3
    assert job["spec"]["ttlSecondsAfterFinished"] == 60
    # template survives untouched by job-level passthrough
    assert job["spec"]["template"]["spec"]["containers"][0]["image"] == "alpine"


# --- golden-file style snapshots (hermetic: no config files involved) ---

GOLDEN_POD = {
    "apiVersion": "v1",
    "kind": "Pod",
    "metadata": {
        "name": "squarepeg-alpine-abc123",
        "labels": {
            "app.kubernetes.io/managed-by": "squarepeg",
            "squarepeg.io/run-id": RUN_ID,
            "squarepeg.io/created-by": "tester",
        },
    },
    "spec": {
        "restartPolicy": "Never",
        "containers": [
            {
                "name": "main",
                "image": "alpine",
                "args": ["echo", "hi"],
                "env": [{"name": "FOO", "value": "bar"}],
                "resources": {
                    "requests": {"cpu": "500m", "memory": "512Mi"},
                    "limits": {"cpu": "500m", "memory": "512Mi"},
                },
            }
        ],
    },
}


def test_golden_pod_snapshot(monkeypatch):
    monkeypatch.setattr("getpass.getuser", lambda: "tester")
    spec = basic_spec(
        args=("echo", "hi"),
        env={"FOO": "bar"},
        resources=ResourceSpec(request_cpu="500m", limit_cpu="500m", request_memory="512Mi", limit_memory="512Mi"),
    )
    pod = build_pod(spec, run_id=RUN_ID)
    assert pod == GOLDEN_POD
    # also assert it round-trips through YAML cleanly, as --dry-run would emit it
    assert yaml.safe_load(yaml.safe_dump(pod)) == GOLDEN_POD

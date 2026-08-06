import yaml
from click.testing import CliRunner
from kubernetes.client.rest import ApiException

import squarepeg.cli as cli_module
from squarepeg.cli import cli
from squarepeg.errors import RunnerError


class FakeSession:
    """Stands in for squarepeg.k8s.session.Session so CLI-level dry-run-server tests never
    touch a real cluster; records the args it was constructed with for assertions."""

    def __init__(self, namespace=None, context=None, quiet=False):
        self.namespace = namespace or "default"
        self.context = context
        self.quiet = quiet
        self.context_name = context or "fake-context"


def test_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "squarepeg" in result.output


def test_run_dry_run_does_not_touch_a_cluster():
    """--dry-run must never require kubeconfig/cluster access; this is what keeps unit tests hermetic."""
    result = CliRunner().invoke(cli, ["run", "--no-default-config", "--dry-run", "alpine"])
    assert result.exit_code == 0
    assert "kind: Pod" in result.output


def _without_run_id(manifest):
    manifest = dict(manifest)
    manifest["metadata"] = dict(manifest["metadata"])
    manifest["metadata"]["labels"] = {
        k: v for k, v in manifest["metadata"]["labels"].items() if k != "squarepeg.io/run-id"
    }
    return manifest


def test_run_dry_run_output_unaffected_by_force_color(monkeypatch):
    """--dry-run's YAML is machine-parseable output and must never be colourised/decorated,
    regardless of $FORCE_COLOR -- only squarepeg's own status chatter goes through ui.py."""
    args = ["run", "--no-default-config", "--name", "squarepeg-pinned-name", "--dry-run", "alpine"]
    plain = CliRunner().invoke(cli, args)
    monkeypatch.setenv("FORCE_COLOR", "1")
    forced = CliRunner().invoke(cli, args)
    assert "\x1b" not in forced.output
    # squarepeg.io/run-id is a fresh random UUID per invocation by design; everything else
    # must be byte-identical regardless of $FORCE_COLOR.
    assert _without_run_id(yaml.safe_load(plain.output)) == _without_run_id(yaml.safe_load(forced.output))


def test_run_missing_image_errors():
    result = CliRunner().invoke(cli, ["run"])
    assert result.exit_code != 0


def test_config_show_no_sources():
    result = CliRunner().invoke(cli, ["config", "show", "--no-default-config"])
    assert result.exit_code == 0


def test_run_dry_run_resolves_env_var_reference_in_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SQUAREPEG_TEST_CLI_SECRET", "regcred")
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        'kubernetes:\n  spec:\n    imagePullSecrets:\n      - {name: "${SQUAREPEG_TEST_CLI_SECRET}"}\n'
    )
    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--config", str(config_file), "--dry-run", "alpine"]
    )
    assert result.exit_code == 0, result.output
    assert "name: regcred" in result.output
    assert "${" not in result.output


def test_run_dry_run_auto_mounts_volume_with_no_v_flag(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "volumes:\n  scratch:\n    emptyDir: {sizeLimit: 10Gi}\n    mount_path: /scratch\n"
    )
    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--config", str(config_file), "--dry-run", "alpine"]
    )
    assert result.exit_code == 0, result.output
    assert "mountPath: /scratch" in result.output
    assert "name: scratch" in result.output
    assert "mount_path" not in result.output  # squarepeg-owned key must not leak into the manifest


def test_run_dry_run_auto_mount_and_cli_v_produce_two_mounts_one_volume(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "volumes:\n  scratch:\n    emptyDir: {sizeLimit: 10Gi}\n    mount_path: /scratch\n"
    )
    result = CliRunner().invoke(
        cli,
        [
            "run",
            "--no-default-config",
            "--config",
            str(config_file),
            "-v",
            "scratch:/elsewhere",
            "--dry-run",
            "alpine",
        ],
    )
    assert result.exit_code == 0, result.output
    manifest = yaml.safe_load(result.output)
    assert len(manifest["spec"]["volumes"]) == 1  # one spec.volumes entry despite two mounts
    assert manifest["spec"]["volumes"][0]["name"] == "scratch"
    mount_paths = {m["mountPath"] for m in manifest["spec"]["containers"][0]["volumeMounts"]}
    assert mount_paths == {"/scratch", "/elsewhere"}


def test_run_dry_run_existing_config_without_mount_path_unaffected(tmp_path):
    """Regression: a volumes.NAME entry with no mount_path behaves exactly as before -- not
    auto-mounted, only available via -v."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("volumes:\n  refdata:\n    persistentVolumeClaim: {claimName: refdata-pvc}\n")
    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--config", str(config_file), "--dry-run", "alpine"]
    )
    assert result.exit_code == 0, result.output
    assert "volumes:" not in result.output
    assert "refdata" not in result.output


def test_run_dry_run_auto_mount_with_no_source_falls_back_to_empty_dir(tmp_path):
    """Regression: mount_path with no k8s volume source (persistentVolumeClaim/emptyDir/etc.)
    must still produce a valid manifest, falling back to emptyDir rather than a typeless volume."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("volumes:\n  scratch:\n    mount_path: /scratch\n")
    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--config", str(config_file), "--dry-run", "alpine"]
    )
    assert result.exit_code == 0, result.output
    manifest = yaml.safe_load(result.output)
    assert manifest["spec"]["volumes"] == [{"name": "scratch", "emptyDir": {}}]


def test_run_dry_run_ambiguous_passthrough_volume_mounts_raises_clear_error(tmp_path):
    """Regression: config passthrough that redeclares volumeMounts for a container whose
    mounts already have a duplicate name (auto-mount + -v at another path) must raise a
    clear error rather than silently dropping one of the mounts."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "volumes:\n"
        "  scratch:\n"
        "    emptyDir: {}\n"
        "    mount_path: /scratch\n"
        "kubernetes:\n"
        "  spec:\n"
        "    containers:\n"
        "      - name: main\n"
        "        volumeMounts:\n"
        "          - {name: scratch, mountPath: /conflict}\n"
    )
    result = CliRunner().invoke(
        cli,
        ["run", "--no-default-config", "--config", str(config_file), "-v", "scratch:/elsewhere", "--dry-run", "alpine"],
    )
    assert result.exit_code != 0
    assert "scratch" in str(result.exception)


def test_run_dry_run_accepts_orphan_sweep_config_keys(tmp_path):
    """Confirms the config -> RunSpec wiring for orphan_sweep/orphan_sweep_min_age doesn't
    break the normal dry-run path (these keys don't affect the rendered manifest at all)."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("orphan_sweep: false\norphan_sweep_min_age: 60\n")
    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--config", str(config_file), "--dry-run", "alpine"]
    )
    assert result.exit_code == 0, result.output
    assert "kind: Pod" in result.output


def test_run_negative_orphan_sweep_min_age_rejected(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("orphan_sweep_min_age: -1\n")
    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--config", str(config_file), "--dry-run", "alpine"]
    )
    assert result.exit_code != 0
    assert "orphan_sweep_min_age" in str(result.exception)


# --- --dry-run-server ---


def test_dry_run_and_dry_run_server_together_rejected():
    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--dry-run", "--dry-run-server", "alpine"]
    )
    assert result.exit_code != 0
    assert "--dry-run" in str(result.exception)
    assert "--dry-run-server" in str(result.exception)


def test_dry_run_server_prints_apiserver_response_not_client_manifest(monkeypatch):
    """The value-add over --dry-run: server-added fields (e.g. a resolved namespace, a UID)
    that only the apiserver's own response would carry, not squarepeg's client-rendered manifest."""
    monkeypatch.setattr(cli_module, "Session", FakeSession)

    def fake_server_dry_run(session, spec, manifest):
        assert isinstance(session, FakeSession)
        response = dict(manifest)
        response["metadata"] = dict(manifest["metadata"], uid="server-assigned-uid", namespace=session.namespace)
        return response

    monkeypatch.setattr(cli_module, "server_dry_run", fake_server_dry_run)

    result = CliRunner().invoke(cli, ["run", "--no-default-config", "--dry-run-server", "alpine"])
    assert result.exit_code == 0, result.output
    parsed = yaml.safe_load(result.output)
    assert parsed["metadata"]["uid"] == "server-assigned-uid"

    dry_run_result = CliRunner().invoke(cli, ["run", "--no-default-config", "--dry-run", "alpine"])
    assert "uid" not in dry_run_result.output


def test_dry_run_server_sends_identical_manifest_to_dry_run(monkeypatch):
    monkeypatch.setattr(cli_module, "Session", FakeSession)
    captured = {}

    def fake_server_dry_run(session, spec, manifest):
        captured["manifest"] = manifest
        return manifest

    monkeypatch.setattr(cli_module, "server_dry_run", fake_server_dry_run)

    args = ["run", "--no-default-config", "--name", "squarepeg-pinned-name"]
    server_result = CliRunner().invoke(cli, [*args, "--dry-run-server", "alpine"])
    assert server_result.exit_code == 0, server_result.output

    dry_run_result = CliRunner().invoke(cli, [*args, "--dry-run", "alpine"])
    assert dry_run_result.exit_code == 0, dry_run_result.output

    # squarepeg.io/run-id is a fresh random UUID per invocation by design; strip it before
    # comparing, since it's the one label that's expected to legitimately differ here.
    assert _without_run_id(captured["manifest"]) == _without_run_id(yaml.safe_load(dry_run_result.output))


def test_dry_run_server_constructs_session_with_context_and_quiet(monkeypatch):
    constructed = {}

    class RecordingFakeSession(FakeSession):
        def __init__(self, namespace=None, context=None, quiet=False):
            constructed["namespace"] = namespace
            constructed["context"] = context
            constructed["quiet"] = quiet
            super().__init__(namespace=namespace, context=context, quiet=quiet)

    monkeypatch.setattr(cli_module, "Session", RecordingFakeSession)
    monkeypatch.setattr(cli_module, "server_dry_run", lambda session, spec, manifest: manifest)

    result = CliRunner().invoke(
        cli, ["run", "--no-default-config", "--context", "my-context", "--quiet", "--dry-run-server", "alpine"]
    )
    assert result.exit_code == 0, result.output
    assert constructed["context"] == "my-context"
    assert constructed["quiet"] is True


def test_dry_run_server_apiserver_rejection_exits_125(monkeypatch):
    monkeypatch.setattr(cli_module, "Session", FakeSession)

    def failing_server_dry_run(session, spec, manifest):
        exc = ApiException(status=403, reason="Forbidden")
        exc.body = '{"message": "pods \\"p\\" is forbidden: failed quota: quota: must specify requests.cpu"}'
        from squarepeg.k8s.apierrors import wrap_api_exception

        raise wrap_api_exception(exc, "failed to validate pod 'p' against the apiserver")

    monkeypatch.setattr(cli_module, "server_dry_run", failing_server_dry_run)

    result = CliRunner().invoke(cli, ["run", "--no-default-config", "--dry-run-server", "alpine"])
    assert result.exit_code != 0
    assert result.exception.exit_code == 125
    assert "ResourceQuota" in str(result.exception)


def test_dry_run_server_no_cluster_connection_exits_125(monkeypatch):
    def raising_session(namespace=None, context=None, quiet=False):
        raise RunnerError("could not load a kubeconfig (...) and doesn't look like it's running in-cluster (...)")

    monkeypatch.setattr(cli_module, "Session", raising_session)

    result = CliRunner().invoke(cli, ["run", "--no-default-config", "--dry-run-server", "alpine"])
    assert result.exit_code != 0
    assert result.exception.exit_code == 125
    assert "kubeconfig" in str(result.exception)


# --- config group: bare 'config' defaults to 'show' ---


def test_config_bare_equals_config_show():
    bare = CliRunner().invoke(cli, ["config", "--no-default-config"])
    show = CliRunner().invoke(cli, ["config", "show", "--no-default-config"])
    assert bare.exit_code == 0, bare.output
    assert show.exit_code == 0, show.output
    assert bare.output == show.output


def test_config_bare_no_sources_message():
    result = CliRunner().invoke(cli, ["config", "--no-default-config"])
    assert result.exit_code == 0, result.output
    assert "no config files loaded" in result.output


def test_config_bare_respects_profile(tmp_path):
    config_file = tmp_path / "config.yaml"
    config_file.write_text("profiles:\n  p:\n    namespace: from-profile\n")
    result = CliRunner().invoke(
        cli, ["config", "--no-default-config", "--config", str(config_file), "--profile", "p"]
    )
    assert result.exit_code == 0, result.output
    assert "from-profile" in result.output


def test_config_option_before_subcommand_matches_bare(tmp_path):
    """Group-level options given before 'show' must still apply -- proves the default_map
    seeding in the group callback actually wires the options through, rather than an
    explicit 'show' silently discarding them."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text("namespace: from-config\n")
    before = CliRunner().invoke(cli, ["config", "--no-default-config", "--config", str(config_file), "show"])
    bare = CliRunner().invoke(cli, ["config", "--no-default-config", "--config", str(config_file)])
    assert before.exit_code == 0, before.output
    assert bare.exit_code == 0, bare.output
    assert before.output == bare.output
    assert "from-config" in before.output


def test_config_explicit_show_option_wins_over_group_level(tmp_path):
    """An explicit 'config show --profile q' must win over a group-level '--profile p'."""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        "profiles:\n  p:\n    namespace: from-p\n  q:\n    namespace: from-q\n"
    )
    result = CliRunner().invoke(
        cli,
        [
            "config",
            "--no-default-config",
            "--config",
            str(config_file),
            "--profile",
            "p",
            "show",
            "--profile",
            "q",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "from-q" in result.output
    assert "from-p" not in result.output


def test_config_unknown_subcommand_errors():
    result = CliRunner().invoke(cli, ["config", "bogus"])
    assert result.exit_code != 0
    assert "No such command 'bogus'" in result.output

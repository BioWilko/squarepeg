import yaml
from click.testing import CliRunner

from squarepeg.cli import cli


def test_version():
    result = CliRunner().invoke(cli, ["--version"])
    assert result.exit_code == 0
    assert "squarepeg" in result.output


def test_run_dry_run_does_not_touch_a_cluster():
    """--dry-run must never require kubeconfig/cluster access; this is what keeps unit tests hermetic."""
    result = CliRunner().invoke(cli, ["run", "--no-default-config", "--dry-run", "alpine"])
    assert result.exit_code == 0
    assert "kind: Pod" in result.output


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

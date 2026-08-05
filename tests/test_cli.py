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

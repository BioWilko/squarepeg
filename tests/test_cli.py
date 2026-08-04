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

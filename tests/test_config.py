import pytest

from squarepeg import config
from squarepeg.errors import ConfigError


def write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    return path


def test_missing_default_file_is_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "does-not-exist.yaml")
    effective, sources = config.load_effective_config()
    assert effective == {}
    assert sources == []


def test_default_file_is_read_when_present(tmp_path, monkeypatch):
    default_path = write(tmp_path / "config.yaml", "namespace: from-default\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", default_path)
    effective, sources = config.load_effective_config()
    assert effective["namespace"] == "from-default"
    assert sources == [(default_path, "default location")]


def test_no_default_config_suppresses_default_file(tmp_path, monkeypatch):
    default_path = write(tmp_path / "config.yaml", "namespace: from-default\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", default_path)
    effective, sources = config.load_effective_config(no_default_config=True)
    assert effective == {}
    assert sources == []


def test_malformed_yaml_reports_path(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    bad = write(tmp_path / "bad.yaml", "namespace: [unterminated\n")
    with pytest.raises(ConfigError, match=str(bad)):
        config.load_effective_config((str(bad),))


def test_empty_file_yields_empty_dict(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    empty = write(tmp_path / "empty.yaml", "")
    effective, _ = config.load_effective_config((str(empty),))
    assert effective == {}


def test_unknown_top_level_key_errors_and_names_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    bad = write(tmp_path / "typo.yaml", "namesapce: oops\n")
    with pytest.raises(ConfigError, match=str(bad)):
        config.load_effective_config((str(bad),))


def test_unknown_key_inside_kubernetes_passthrough_is_accepted(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    ok = write(tmp_path / "ok.yaml", "kubernetes:\n  spec:\n    somethingBrandNew: true\n")
    effective, _ = config.load_effective_config((str(ok),))
    assert effective["kubernetes"]["spec"]["somethingBrandNew"] is True


def test_missing_cli_config_path_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    with pytest.raises(ConfigError, match="--config"):
        config.load_effective_config((str(tmp_path / "nope.yaml"),))


def test_missing_env_config_path_errors(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv(config.ENV_VAR, str(tmp_path / "nope.yaml"))
    with pytest.raises(ConfigError, match=config.ENV_VAR):
        config.load_effective_config()


def test_repeatable_config_merges_in_order_later_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    first = write(tmp_path / "a.yaml", "namespace: from-a\ntimeout: 100\n")
    second = write(tmp_path / "b.yaml", "namespace: from-b\n")
    effective, _ = config.load_effective_config((str(first), str(second)))
    assert effective["namespace"] == "from-b"
    assert effective["timeout"] == 100  # survives from the earlier file


def test_three_files_fold_left_to_right(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    a = write(tmp_path / "a.yaml", "namespace: a\n")
    b = write(tmp_path / "b.yaml", "namespace: b\n")
    c = write(tmp_path / "c.yaml", "namespace: c\n")
    effective, _ = config.load_effective_config((str(a), str(b), str(c)))
    assert effective["namespace"] == "c"


def test_env_var_colon_separated_multiple_paths(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    a = write(tmp_path / "a.yaml", "namespace: from-a\n")
    b = write(tmp_path / "b.yaml", "namespace: from-b\n")
    monkeypatch.setenv(config.ENV_VAR, f"{a}:{b}")
    effective, sources = config.load_effective_config()
    assert effective["namespace"] == "from-b"
    assert [s[0] for s in sources] == [a, b]


def test_env_var_skips_empty_segments(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    a = write(tmp_path / "a.yaml", "namespace: from-a\n")
    monkeypatch.setenv(config.ENV_VAR, f":{a}:")
    effective, sources = config.load_effective_config()
    assert effective["namespace"] == "from-a"
    assert len(sources) == 1


def test_full_layering_order_lowest_to_highest(tmp_path, monkeypatch):
    default_path = write(tmp_path / "default.yaml", "namespace: from-default\n")
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", default_path)
    env_file = write(tmp_path / "env.yaml", "namespace: from-env\n")
    cli_file = write(tmp_path / "cli.yaml", "namespace: from-cli-config\n")
    monkeypatch.setenv(config.ENV_VAR, str(env_file))

    effective, sources = config.load_effective_config()
    assert effective["namespace"] == "from-env"  # env beats default

    effective, sources = config.load_effective_config((str(cli_file),))
    assert effective["namespace"] == "from-cli-config"  # --config beats env and default
    assert [s[0] for s in sources] == [default_path, env_file, cli_file]


def test_named_env_list_merges_across_layers(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    a = write(
        tmp_path / "a.yaml",
        "kubernetes:\n  spec:\n    containers:\n      - name: main\n        env:\n          - {name: A, value: '1'}\n",
    )
    b = write(
        tmp_path / "b.yaml",
        "kubernetes:\n  spec:\n    containers:\n      - name: main\n        env:\n          - {name: B, value: '2'}\n",
    )
    effective, _ = config.load_effective_config((str(a), str(b)))
    env = {e["name"]: e["value"] for e in effective["kubernetes"]["spec"]["containers"][0]["env"]}
    assert env == {"A": "1", "B": "2"}


def test_tolerations_replaced_wholesale_by_later_file(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    a = write(tmp_path / "a.yaml", "kubernetes:\n  spec:\n    tolerations:\n      - {key: a}\n")
    b = write(tmp_path / "b.yaml", "kubernetes:\n  spec:\n    tolerations:\n      - {key: b}\n")
    effective, _ = config.load_effective_config((str(a), str(b)))
    assert effective["kubernetes"]["spec"]["tolerations"] == [{"key": "b"}]


def test_same_named_volume_from_different_files_deep_merges(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    a = write(tmp_path / "a.yaml", "volumes:\n  scratch:\n    emptyDir: {sizeLimit: 10Gi}\n")
    b = write(tmp_path / "b.yaml", "volumes:\n  refdata:\n    persistentVolumeClaim: {claimName: pvc}\n")
    effective, _ = config.load_effective_config((str(a), str(b)))
    assert set(effective["volumes"]) == {"scratch", "refdata"}


def test_profiles_same_name_across_files_deep_merge(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    site = write(
        tmp_path / "site.yaml",
        "profiles:\n  gpu:\n    kubernetes:\n      spec:\n        nodeSelector: {gpu: 'true'}\n",
    )
    user = write(tmp_path / "user.yaml", "profiles:\n  gpu:\n    defaults: {memory: 32g}\n")
    effective, _ = config.load_effective_config((str(site), str(user)))
    gpu = effective["profiles"]["gpu"]
    assert gpu["kubernetes"]["spec"]["nodeSelector"] == {"gpu": "true"}
    assert gpu["defaults"]["memory"] == "32g"


def test_select_profile_merges_over_base(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    doc = write(
        tmp_path / "doc.yaml",
        "namespace: base-ns\n"
        "defaults: {cpus: '1'}\n"
        "profiles:\n  gpu:\n    defaults: {cpus: '8'}\n",
    )
    effective, _ = config.load_effective_config((str(doc),))
    resolved = config.select_profile(effective, "gpu")
    assert resolved["namespace"] == "base-ns"
    assert resolved["defaults"]["cpus"] == "8"
    assert "profiles" not in resolved


def test_select_profile_no_profile_name_drops_profiles_key(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    doc = write(tmp_path / "doc.yaml", "namespace: base-ns\nprofiles:\n  gpu: {}\n")
    effective, _ = config.load_effective_config((str(doc),))
    resolved = config.select_profile(effective, None)
    assert resolved == {"namespace": "base-ns"}


def test_select_unknown_profile_errors_and_lists_available(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    doc = write(tmp_path / "doc.yaml", "profiles:\n  gpu: {}\n  highmem: {}\n")
    effective, _ = config.load_effective_config((str(doc),))
    with pytest.raises(ConfigError, match="gpu"):
        config.select_profile(effective, "nonexistent")


def test_nested_profiles_key_inside_profile_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    bad = write(tmp_path / "bad.yaml", "profiles:\n  gpu:\n    profiles:\n      nested: {}\n")
    with pytest.raises(ConfigError, match="nested"):
        config.load_effective_config((str(bad),))


# --- ${VAR} interpolation, through the full loader ---


def test_top_level_string_value_interpolated(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("SQUAREPEG_TEST_NS", "from-env")
    doc = write(tmp_path / "doc.yaml", "namespace: ${SQUAREPEG_TEST_NS}\n")
    effective, _ = config.load_effective_config((str(doc),))
    assert effective["namespace"] == "from-env"


def test_interpolation_inside_kubernetes_passthrough(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("SQUAREPEG_TEST_SECRET", "regcred")
    doc = write(
        tmp_path / "doc.yaml",
        "kubernetes:\n  spec:\n    imagePullSecrets:\n      - {name: \"${SQUAREPEG_TEST_SECRET}\"}\n",
    )
    effective, _ = config.load_effective_config((str(doc),))
    assert effective["kubernetes"]["spec"]["imagePullSecrets"] == [{"name": "regcred"}]


def test_missing_var_error_names_file_and_variable(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.delenv("SQUAREPEG_TEST_UNSET_CONFIG_VAR", raising=False)
    bad = write(tmp_path / "bad.yaml", "namespace: ${SQUAREPEG_TEST_UNSET_CONFIG_VAR}\n")
    with pytest.raises(ConfigError) as exc_info:
        config.load_effective_config((str(bad),))
    message = str(exc_info.value)
    assert "SQUAREPEG_TEST_UNSET_CONFIG_VAR" in message
    assert str(bad) in message


def test_two_layered_files_each_using_a_different_var(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("SQUAREPEG_TEST_A", "a-value")
    monkeypatch.setenv("SQUAREPEG_TEST_B", "b-value")
    a = write(tmp_path / "a.yaml", "namespace: ${SQUAREPEG_TEST_A}\n")
    b = write(tmp_path / "b.yaml", "defaults: {workdir: \"${SQUAREPEG_TEST_B}\"}\n")
    effective, _ = config.load_effective_config((str(a), str(b)))
    assert effective["namespace"] == "a-value"
    assert effective["defaults"]["workdir"] == "b-value"


def test_later_file_resolved_value_wins_over_earlier(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("SQUAREPEG_TEST_A", "from-a")
    monkeypatch.setenv("SQUAREPEG_TEST_B", "from-b")
    a = write(tmp_path / "a.yaml", "namespace: ${SQUAREPEG_TEST_A}\n")
    b = write(tmp_path / "b.yaml", "namespace: ${SQUAREPEG_TEST_B}\n")
    effective, _ = config.load_effective_config((str(a), str(b)))
    assert effective["namespace"] == "from-b"


def test_timeout_string_from_interpolation_coerces_to_int(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    doc = write(tmp_path / "doc.yaml", "timeout: ${SQUAREPEG_TEST_TIMEOUT:-300}\n")
    effective, _ = config.load_effective_config((str(doc),))
    assert config.coerce_int(effective["timeout"], "timeout") == 300


def test_cleanup_false_string_from_interpolation_coerces_to_bool(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("SQUAREPEG_TEST_CLEANUP", "false")
    doc = write(tmp_path / "doc.yaml", "cleanup: ${SQUAREPEG_TEST_CLEANUP}\n")
    effective, _ = config.load_effective_config((str(doc),))
    assert config.coerce_bool(effective["cleanup"], "cleanup") is False


def test_coerce_bool_rejects_garbage():
    with pytest.raises(ConfigError, match="cleanup"):
        config.coerce_bool("maybe", "cleanup")


def test_coerce_int_rejects_garbage():
    with pytest.raises(ConfigError, match="timeout"):
        config.coerce_int("soon", "timeout")


def test_reference_in_unselected_profile_still_errors(tmp_path, monkeypatch):
    """Interpolation is eager over the whole document -- matches validate_document,
    which already validates every profile regardless of selection."""
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.delenv("SQUAREPEG_TEST_UNSET_PROFILE_VAR", raising=False)
    doc = write(
        tmp_path / "doc.yaml",
        "profiles:\n  gpu:\n    namespace: ${SQUAREPEG_TEST_UNSET_PROFILE_VAR}\n",
    )
    with pytest.raises(ConfigError, match="SQUAREPEG_TEST_UNSET_PROFILE_VAR"):
        config.load_effective_config((str(doc),))


def test_unknown_key_still_rejected_when_value_has_a_reference(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("SQUAREPEG_TEST_TYPO_VALUE", "whatever")
    bad = write(tmp_path / "bad.yaml", "namesapce: ${SQUAREPEG_TEST_TYPO_VALUE}\n")
    with pytest.raises(ConfigError, match="namesapce"):
        config.load_effective_config((str(bad),))


def test_config_without_dollar_signs_behaves_identically(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    doc = write(tmp_path / "doc.yaml", "namespace: plain-ns\ndefaults: {cpus: '2'}\n")
    effective, _ = config.load_effective_config((str(doc),))
    assert effective == {"namespace": "plain-ns", "defaults": {"cpus": "2"}}


# --- volumes.NAME auto-mount fields: mount_path / read_only ---


def test_volumes_entry_with_valid_mount_path_loads_fine(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    doc = write(tmp_path / "doc.yaml", "volumes:\n  scratch:\n    emptyDir: {}\n    mount_path: /scratch\n")
    effective, _ = config.load_effective_config((str(doc),))
    assert effective["volumes"]["scratch"]["mount_path"] == "/scratch"


def test_volumes_entry_mount_path_must_be_absolute(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    bad = write(tmp_path / "bad.yaml", "volumes:\n  scratch:\n    emptyDir: {}\n    mount_path: scratch\n")
    with pytest.raises(ConfigError, match="mount_path"):
        config.load_effective_config((str(bad),))


def test_volumes_entry_read_only_must_be_bool_coercible(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    bad = write(
        tmp_path / "bad.yaml", "volumes:\n  scratch:\n    emptyDir: {}\n    mount_path: /x\n    read_only: maybe\n"
    )
    with pytest.raises(ConfigError, match="read_only"):
        config.load_effective_config((str(bad),))


def test_volumes_entry_read_only_accepts_interpolated_bool_string(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    monkeypatch.setenv("SQUAREPEG_TEST_RO", "true")
    doc = write(
        tmp_path / "doc.yaml",
        "volumes:\n  scratch:\n    emptyDir: {}\n    mount_path: /x\n    read_only: ${SQUAREPEG_TEST_RO}\n",
    )
    effective, _ = config.load_effective_config((str(doc),))
    assert config.coerce_bool(effective["volumes"]["scratch"]["read_only"], "x") is True


def test_volumes_entry_without_mount_path_still_valid(tmp_path, monkeypatch):
    """Backward compatibility: existing configs (no mount_path/read_only at all) load unchanged."""
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    doc = write(
        tmp_path / "doc.yaml",
        "volumes:\n  refdata:\n    persistentVolumeClaim: {claimName: refdata-pvc, readOnly: true}\n",
    )
    effective, _ = config.load_effective_config((str(doc),))
    assert "mount_path" not in effective["volumes"]["refdata"]


def test_volumes_value_must_be_a_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    bad = write(tmp_path / "bad.yaml", "volumes:\n  - not-a-mapping\n")
    with pytest.raises(ConfigError, match="volumes"):
        config.load_effective_config((str(bad),))


def test_volumes_entry_value_must_be_a_mapping(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DEFAULT_CONFIG_PATH", tmp_path / "missing.yaml")
    bad = write(tmp_path / "bad.yaml", "volumes:\n  scratch: not-a-mapping\n")
    with pytest.raises(ConfigError, match="scratch"):
        config.load_effective_config((str(bad),))

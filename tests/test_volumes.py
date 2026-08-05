import pytest

from squarepeg.errors import ConfigError, UsageError
from squarepeg.volumes import HostPathMount, VolumeMount, auto_mounts_from_config, parse_volume_flag


def test_anonymous_volume():
    v = parse_volume_flag("/data", config_volumes={}, allow_host_path_mounts=False)
    assert isinstance(v, VolumeMount)
    assert v.container_path == "/data"
    assert v.source is None
    assert not v.read_only


def test_named_volume_resolves_from_config():
    config_volumes = {"refdata": {"persistentVolumeClaim": {"claimName": "refdata-pvc"}}}
    v = parse_volume_flag("refdata:/ref", config_volumes=config_volumes, allow_host_path_mounts=False)
    assert v.source == config_volumes["refdata"]
    assert v.container_path == "/ref"


def test_named_volume_unconfigured_falls_back_to_emptydir():
    v = parse_volume_flag("scratch:/tmp/scratch", config_volumes={}, allow_host_path_mounts=False)
    assert v.source is None


def test_read_only_suffix():
    v = parse_volume_flag("refdata:/ref:ro", config_volumes={"refdata": {}}, allow_host_path_mounts=False)
    assert v.read_only


def test_read_write_suffix():
    v = parse_volume_flag("refdata:/ref:rw", config_volumes={"refdata": {}}, allow_host_path_mounts=False)
    assert not v.read_only


def test_unsupported_mount_option_rejected():
    with pytest.raises(UsageError):
        parse_volume_flag("refdata:/ref:z", config_volumes={"refdata": {}}, allow_host_path_mounts=False)


def test_too_many_fields_rejected():
    with pytest.raises(UsageError):
        parse_volume_flag("a:b:c:d", config_volumes={}, allow_host_path_mounts=False)


def test_host_bind_mount_rejected_by_default():
    with pytest.raises(UsageError):
        parse_volume_flag("/host/path:/container/path", config_volumes={}, allow_host_path_mounts=False)


def test_host_bind_mount_allowed_when_opted_in():
    v = parse_volume_flag("/host/path:/container/path", config_volumes={}, allow_host_path_mounts=True)
    assert isinstance(v, HostPathMount)
    assert v.host_path == "/host/path"


def test_invalid_volume_name_rejected():
    with pytest.raises(UsageError):
        parse_volume_flag("bad name!:/x", config_volumes={}, allow_host_path_mounts=False)


# --- auto-mounted volumes (volumes.NAME with a mount_path in config) ---


def test_auto_mount_produces_volume_mount():
    config_volumes = {
        "refdata": {"persistentVolumeClaim": {"claimName": "refdata-pvc"}, "mount_path": "/ref"},
    }
    mounts = auto_mounts_from_config(config_volumes)
    assert len(mounts) == 1
    assert mounts[0].container_path == "/ref"
    assert mounts[0].volume_name == "refdata"
    assert mounts[0].read_only is False
    assert mounts[0].source == {"persistentVolumeClaim": {"claimName": "refdata-pvc"}}


def test_entry_without_mount_path_produces_no_auto_mount():
    config_volumes = {"refdata": {"persistentVolumeClaim": {"claimName": "refdata-pvc"}}}
    assert auto_mounts_from_config(config_volumes) == []


def test_auto_mount_read_only_true():
    config_volumes = {"refdata": {"emptyDir": {}, "mount_path": "/ref", "read_only": True}}
    mounts = auto_mounts_from_config(config_volumes)
    assert mounts[0].read_only is True


def test_auto_mount_read_only_coerces_string_from_interpolation():
    config_volumes = {"refdata": {"emptyDir": {}, "mount_path": "/ref", "read_only": "true"}}
    mounts = auto_mounts_from_config(config_volumes)
    assert mounts[0].read_only is True


def test_auto_mount_read_only_rejects_garbage():
    config_volumes = {"refdata": {"emptyDir": {}, "mount_path": "/ref", "read_only": "maybe"}}
    with pytest.raises(ConfigError):
        auto_mounts_from_config(config_volumes)


def test_auto_mount_sanitizes_config_key_name():
    config_volumes = {"Ref_Data!": {"emptyDir": {}, "mount_path": "/ref"}}
    mounts = auto_mounts_from_config(config_volumes)
    assert mounts[0].volume_name == "ref-data"


def test_auto_mounts_multiple_entries_deterministic_order():
    config_volumes = {
        "a": {"emptyDir": {}, "mount_path": "/a"},
        "b": {"emptyDir": {}, "mount_path": "/b"},
    }
    mounts = auto_mounts_from_config(config_volumes)
    assert [m.container_path for m in mounts] == ["/a", "/b"]


def test_auto_mounts_from_empty_or_none_config():
    assert auto_mounts_from_config({}) == []
    assert auto_mounts_from_config(None) == []


def test_auto_mount_source_excludes_squarepeg_owned_keys():
    config_volumes = {"scratch": {"emptyDir": {"sizeLimit": "10Gi"}, "mount_path": "/scratch", "read_only": False}}
    mounts = auto_mounts_from_config(config_volumes)
    assert mounts[0].source == {"emptyDir": {"sizeLimit": "10Gi"}}
    assert "mount_path" not in mounts[0].source
    assert "read_only" not in mounts[0].source


def test_cli_v_reference_strips_squarepeg_owned_keys_from_source():
    """A volume that's ALSO auto-mounted (has mount_path/read_only) must not leak those
    squarepeg-only keys into the k8s source when referenced via -v too."""
    config_volumes = {"scratch": {"emptyDir": {"sizeLimit": "10Gi"}, "mount_path": "/scratch", "read_only": True}}
    v = parse_volume_flag("scratch:/elsewhere", config_volumes=config_volumes, allow_host_path_mounts=False)
    assert v.source == {"emptyDir": {"sizeLimit": "10Gi"}}

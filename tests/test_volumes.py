import pytest

from squarepeg.errors import UsageError
from squarepeg.volumes import HostPathMount, VolumeMount, parse_volume_flag


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

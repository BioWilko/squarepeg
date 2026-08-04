import re
from dataclasses import dataclass

from squarepeg.errors import UsageError

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


@dataclass
class VolumeMount:
    volume_name: str
    container_path: str
    read_only: bool
    source: dict | None  # None -> emptyDir; dict -> config-supplied volume source; "hostPath" marker handled by kind


@dataclass
class HostPathMount(VolumeMount):
    host_path: str = ""


def _sanitize_volume_name(raw: str) -> str:
    name = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-")
    return name or "vol"


def parse_volume_flag(raw: str, *, config_volumes: dict, allow_host_path_mounts: bool) -> VolumeMount:
    """Parse a single -v/--volume flag value into a VolumeMount.

    Forms:
      /container/path                     -> anonymous emptyDir
      NAME:/container/path[:ro|:rw]        -> config-defined (or emptyDir) named volume
      /host/path:/container/path[:ro|:rw]  -> host bind mount (error unless opted in)
    """
    parts = raw.split(":")
    if len(parts) == 1:
        container_path = parts[0]
        read_only = False
        name = _sanitize_volume_name(container_path)
        return VolumeMount(volume_name=name, container_path=container_path, read_only=read_only, source=None)

    if len(parts) not in (2, 3):
        raise UsageError(f"invalid -v/--volume value {raw!r}: unsupported number of ':'-separated fields")

    source_spec, container_path = parts[0], parts[1]
    read_only = False
    if len(parts) == 3:
        mode = parts[2]
        if mode == "ro":
            read_only = True
        elif mode == "rw":
            read_only = False
        else:
            raise UsageError(
                f"invalid -v/--volume value {raw!r}: unsupported mount option {mode!r} (only 'ro'/'rw' are supported)"
            )

    if source_spec.startswith("/") or source_spec.startswith("."):
        if not allow_host_path_mounts:
            raise UsageError(
                f"host bind mounts are not supported (got {raw!r}): a remote cluster's nodes are not your host. "
                "Declare a named volume backed by a PVC in your config's 'volumes:' section instead, "
                "or set 'allow_host_path_mounts: true' in config if you are targeting a local cluster."
            )
        name = _sanitize_volume_name(container_path)
        return HostPathMount(
            volume_name=name, container_path=container_path, read_only=read_only, source=None, host_path=source_spec
        )

    if not _NAME_RE.match(source_spec):
        raise UsageError(f"invalid volume name {source_spec!r} in -v/--volume value {raw!r}")

    source = config_volumes.get(source_spec)
    name = _sanitize_volume_name(source_spec)
    return VolumeMount(volume_name=name, container_path=container_path, read_only=read_only, source=source)

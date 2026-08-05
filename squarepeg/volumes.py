import re
from dataclasses import dataclass

from squarepeg.config import coerce_bool
from squarepeg.errors import UsageError

_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")

# keys squarepeg itself reads from a volumes.NAME config entry; everything else is
# an opaque k8s volume source (persistentVolumeClaim, emptyDir, etc.) passed through verbatim
_SQUAREPEG_OWNED_VOLUME_KEYS = {"mount_path", "read_only"}


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


def _volume_source_from_entry(entry: dict) -> dict | None:
    """Strip squarepeg-owned keys (mount_path, read_only) from a config volumes.NAME entry,
    leaving just the k8s volume source (persistentVolumeClaim, emptyDir, etc.). Returns None
    (rather than {}) if nothing is left, so callers fall back to emptyDir the same way an
    entirely unconfigured volume name already does."""
    source = {k: v for k, v in entry.items() if k not in _SQUAREPEG_OWNED_VOLUME_KEYS}
    return source or None


def auto_mounts_from_config(config_volumes: dict) -> list[VolumeMount]:
    """Build a VolumeMount for every volumes.NAME config entry that declares 'mount_path' --
    these are mounted on every run automatically, without the user passing -v."""
    mounts = []
    for raw_name, entry in (config_volumes or {}).items():
        if not isinstance(entry, dict):
            continue
        mount_path = entry.get("mount_path")
        if not mount_path:
            continue
        read_only = coerce_bool(entry.get("read_only", False), f"volumes.{raw_name}.read_only")
        mounts.append(
            VolumeMount(
                volume_name=_sanitize_volume_name(raw_name),
                container_path=mount_path,
                read_only=read_only,
                source=_volume_source_from_entry(entry),
            )
        )
    return mounts


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

    raw_entry = config_volumes.get(source_spec)
    source = _volume_source_from_entry(raw_entry) if isinstance(raw_entry, dict) else raw_entry
    name = _sanitize_volume_name(source_spec)
    return VolumeMount(volume_name=name, container_path=container_path, read_only=read_only, source=source)

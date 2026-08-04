import math
import re

from squarepeg.errors import UsageError

_MEMORY_RE = re.compile(r"^(\d+(?:\.\d+)?)(b|k|kb|m|mb|g|gb)?$", re.IGNORECASE)
_MEMORY_MULTIPLIERS = {
    "b": 1,
    "k": 1024,
    "kb": 1024,
    "m": 1024**2,
    "mb": 1024**2,
    "g": 1024**3,
    "gb": 1024**3,
}
_K8S_BINARY_SUFFIX = {
    1: "",
    1024: "Ki",
    1024**2: "Mi",
    1024**3: "Gi",
}


def docker_memory_to_k8s(value: str) -> str:
    """Convert a docker-style memory string (e.g. '512m', '1g') to a k8s quantity.

    Docker units are 1024-based ('m' = mebibyte), unlike k8s where a bare 'm'
    suffix means milli. Passing docker strings through unconverted silently
    produces a nonsense (near-zero) memory limit.
    """
    match = _MEMORY_RE.match(value.strip())
    if not match:
        raise UsageError(f"invalid memory value {value!r}: expected e.g. '512m', '1g', or a byte count")
    number, suffix = match.groups()
    number = float(number)
    if number < 0:
        raise UsageError(f"invalid memory value {value!r}: must not be negative")
    multiplier = _MEMORY_MULTIPLIERS.get((suffix or "b").lower(), 1)
    total_bytes = number * multiplier

    for step, k8s_suffix in sorted(_K8S_BINARY_SUFFIX.items(), reverse=True):
        if step > 1 and total_bytes % step == 0 and total_bytes != 0:
            return f"{int(total_bytes // step)}{k8s_suffix}"
    return str(int(total_bytes))


def docker_cpus_to_k8s(value: str) -> str:
    """Convert a docker-style --cpus value (fractional cores) to a k8s CPU quantity."""
    try:
        cpus = float(value)
    except ValueError as exc:
        raise UsageError(f"invalid cpus value {value!r}: must be a number") from exc
    if cpus < 0:
        raise UsageError(f"invalid cpus value {value!r}: must not be negative")
    if cpus == int(cpus):
        return str(int(cpus))
    millicores = math.ceil(cpus * 1000)
    return f"{millicores}m"

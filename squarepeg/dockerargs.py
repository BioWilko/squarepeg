import os

from squarepeg.errors import UnsupportedFlagError, UsageError

# flag -> why it can't work the same way on a remote cluster, and what to do instead
UNSUPPORTED: dict[str, str] = {
    "--network": "container networking is a cluster-level concept; use the 'kubernetes' passthrough "
    "section in your config (spec.dnsPolicy, spec.hostNetwork, etc.) if you need to control it",
    "--privileged": "use 'kubernetes.spec.containers[].securityContext.privileged' in your config",
    "--gpus": "request GPUs via 'kubernetes.spec.containers[].resources' (e.g. 'nvidia.com/gpu: 1') "
    "in your config",
    "-p": "there is no host to publish a port to on a remote cluster; use a Kubernetes Service instead",
    "--publish": "there is no host to publish a port to on a remote cluster; use a Kubernetes Service instead",
    "-d": "squarepeg always attaches to stream output; there is no detached mode",
    "--detach": "squarepeg always attaches to stream output; there is no detached mode",
    "--restart": "restart behaviour is controlled by --mode and, for jobs, 'job.spec.backoffLimit' in config",
    "-u": "use 'kubernetes.spec.containers[].securityContext.runAsUser' in your config",
    "--user": "use 'kubernetes.spec.containers[].securityContext.runAsUser' in your config",
    "--mount": "use -v/--volume, or the 'volumes' section in your config for anything more complex",
    "--cap-add": "use 'kubernetes.spec.containers[].securityContext.capabilities' in your config",
    "--cap-drop": "use 'kubernetes.spec.containers[].securityContext.capabilities' in your config",
    "--add-host": "use 'kubernetes.spec.hostAliases' in your config",
    "--link": "container linking has no equivalent on Kubernetes; use a Service and DNS instead",
    "--platform": "node/architecture selection is done via 'kubernetes.spec.nodeSelector' in your config",
    "--env-file": "pass variables individually with -e/--env, or set 'defaults.env' in your config",
    "--hostname": "use 'kubernetes.spec.hostname' in your config",
    "--tmpfs": "use an emptyDir volume (-v /path) or the 'volumes' section in your config",
}


def parse_env_entries(entries: tuple[str, ...]) -> dict[str, str]:
    """Parse repeated -e/--env values into a dict, docker-style: KEY=VALUE, or bare KEY
    to inherit the value from the local environment."""
    env: dict[str, str] = {}
    for entry in entries:
        if "=" in entry:
            key, value = entry.split("=", 1)
        else:
            key = entry
            if key not in os.environ:
                raise UsageError(f"-e/--env {key!r} has no value and is not set in the local environment")
            value = os.environ[key]
        if not key:
            raise UsageError(f"invalid -e/--env value {entry!r}: empty key")
        env[key] = value
    return env


def check_supported_image(image: str) -> None:
    if image.startswith("-"):
        raise UnsupportedFlagError(
            f"{image!r} looks like a flag, not an image: if this is a squarepeg or docker option it is "
            "not supported, or it was placed before the image name"
        )


def reject_unsupported(name: str) -> None:
    reason = UNSUPPORTED.get(name)
    if reason is not None:
        raise UnsupportedFlagError(f"{name} is not supported by squarepeg: {reason}")

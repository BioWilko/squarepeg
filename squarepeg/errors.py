class SquarepegError(Exception):
    """Base class for all squarepeg errors that map to a process exit code."""

    exit_code = 1


class UsageError(SquarepegError):
    """Bad CLI input: malformed flag value, unsupported flag, etc."""

    exit_code = 2


class UnsupportedFlagError(UsageError):
    pass


class ConfigError(SquarepegError):
    """Config file missing, malformed, or invalid."""

    exit_code = 2


class RunnerError(SquarepegError):
    """The tool itself failed to run the container to completion."""

    exit_code = 125


class ApiError(RunnerError):
    """A Kubernetes API call failed; wraps kubernetes.client.ApiException."""


class InterruptError(SquarepegError):
    exit_code = 130

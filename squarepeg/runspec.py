from dataclasses import dataclass, field

from squarepeg.volumes import VolumeMount


@dataclass
class ResourceSpec:
    request_cpu: str | None = None
    limit_cpu: str | None = None
    request_memory: str | None = None
    limit_memory: str | None = None


@dataclass
class RunSpec:
    image: str
    args: tuple[str, ...] = ()
    entrypoint: tuple[str, ...] | None = None
    env: dict[str, str] = field(default_factory=dict)
    workdir: str | None = None
    volumes: list[VolumeMount] = field(default_factory=list)
    tty: bool = False
    stdin: bool = False
    pull_policy: str | None = None  # Always | IfNotPresent | Never

    mode: str = "pod"  # pod | job
    namespace: str | None = None
    name: str | None = None
    container_name: str = "main"

    resources: ResourceSpec = field(default_factory=ResourceSpec)

    cleanup: bool = True
    timeout: int = 300
    quiet: bool = False

    # JSON-pointer-ish paths the CLI explicitly set, used to make config
    # passthrough (Phase B, see merge.py) lose to explicit CLI flags.
    claims: set[str] = field(default_factory=set)

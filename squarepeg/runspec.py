from dataclasses import dataclass, field

from squarepeg.k8s.orphans import DEFAULT_MIN_AGE_SECONDS
from squarepeg.volumes import VolumeMount

DEFAULT_GPU_RESOURCE = "nvidia.com/gpu"
DEFAULT_GPU_RUNTIME_CLASS = "nvidia"


@dataclass
class ResourceSpec:
    request_cpu: str | None = None
    limit_cpu: str | None = None
    request_memory: str | None = None
    limit_memory: str | None = None
    # extended resources can't be overcommitted, so one value is both the request and limit
    gpus: int | None = None
    gpu_resource: str = DEFAULT_GPU_RESOURCE
    # some distros (e.g. k3s/RKE2 with the NVIDIA toolkit) need this set on GPU pods; None disables
    gpu_runtime_class: str | None = DEFAULT_GPU_RUNTIME_CLASS


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
    orphan_sweep: bool = True
    orphan_sweep_min_age: int = DEFAULT_MIN_AGE_SECONDS

    # JSON-pointer-ish paths the CLI explicitly set, used to make config
    # passthrough (Phase B, see merge.py) lose to explicit CLI flags.
    claims: set[str] = field(default_factory=set)

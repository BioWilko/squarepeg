import secrets

import click
import yaml

from squarepeg import __version__, ui
from squarepeg.config import (
    coerce_bool,
    coerce_int,
    load_effective_config,
    select_profile,
)
from squarepeg.dockerargs import (
    check_supported_image,
    parse_env_entries,
    reject_unsupported,
)
from squarepeg.errors import RunnerError, SquarepegError, UsageError
from squarepeg.k8s.dryrun import server_dry_run
from squarepeg.k8s.orphans import DEFAULT_MIN_AGE_SECONDS, clean_orphans
from squarepeg.k8s.runner import run_manifest
from squarepeg.k8s.session import Session
from squarepeg.labels import RUN_ID_LABEL
from squarepeg.log import chatter
from squarepeg.manifest import build_job, build_pod
from squarepeg.naming import generate_name, validate_rfc1123
from squarepeg.quantities import docker_cpus_to_k8s, docker_memory_to_k8s
from squarepeg.runspec import ResourceSpec, RunSpec
from squarepeg.volumes import auto_mounts_from_config, parse_volume_flag

_CONFIG_OPTIONS = [
    click.option(
        "--config",
        "config_paths",
        multiple=True,
        type=click.Path(),
        help="repeatable, layered left-to-right",
    ),
    click.option("--no-default-config", "no_default_config", is_flag=True),
    click.option("--profile", "profile", default=None),
]


def _add_config_options(cmd):
    for option in _CONFIG_OPTIONS:
        cmd = option(cmd)
    return cmd


def _resolve_config(config_paths, no_default_config, profile):
    effective, sources = load_effective_config(
        config_paths, no_default_config=no_default_config
    )
    resolved = select_profile(effective, profile)
    return resolved, sources


def _unsupported_callback(ctx, param, value):
    if value:
        reject_unsupported(param.opts[0])
    return value


# flag names -> whether the flag takes a value (False) or is a boolean switch (True)
_UNSUPPORTED_GROUPS: list[tuple[list[str], bool]] = [
    (["--network"], False),
    (["--privileged"], True),
    (["--gpus"], False),
    (["-p", "--publish"], False),
    (["-d", "--detach"], True),
    (["--restart"], False),
    (["-u", "--user"], False),
    (["--mount"], False),
    (["--cap-add"], False),
    (["--cap-drop"], False),
    (["--add-host"], False),
    (["--link"], False),
    (["--platform"], False),
    (["--env-file"], False),
    (["--hostname"], False),
    (["--tmpfs"], False),
]


def _add_unsupported_options(cmd):
    for flag_names, is_flag in _UNSUPPORTED_GROUPS:
        cmd = click.option(
            *flag_names,
            is_flag=is_flag,
            hidden=True,
            expose_value=False,
            callback=_unsupported_callback,
        )(cmd)
    return cmd


_LOGO = r"""
   _____                                             
  / ___/____ ___  ______ _________  ____  ___  ____ _
  \__ \/ __ `/ / / / __ `/ ___/ _ \/ __ \/ _ \/ __ `/
 ___/ / /_/ / /_/ / /_/ / /  /  __/ /_/ /  __/ /_/ / 
/____/\__, /\__,_/\__,_/_/   \___/ .___/\___/\__, /  
        /_/                     /_/         /____/   
""".strip("\n")

class _LogoGroup(click.Group):
    """Prints the ASCII logo above the 'Usage:' line, rather than as part of the help body."""

    def format_usage(self, ctx, formatter):
        formatter.write(_LOGO + "\n\n")
        super().format_usage(ctx, formatter)


@click.group(cls=_LogoGroup)
@click.version_option(__version__, prog_name="squarepeg")
def cli():
    """Run a docker-run-style command as a Kubernetes Pod or Job."""


@cli.command(
    context_settings={"allow_interspersed_args": False, "ignore_unknown_options": True},
)
@click.option("-e", "--env", "env_entries", multiple=True, help="KEY=VALUE, repeatable")
@click.option(
    "-v",
    "--volume",
    "volume_entries",
    multiple=True,
    help="[NAME|/host]:/container[:ro|rw], repeatable",
)
@click.option("--name", "name", default=None, help="Pod/Job name")
@click.option("-w", "--workdir", "workdir", default=None)
@click.option(
    "--entrypoint",
    "entrypoint",
    default=None,
    help="Override the image ENTRYPOINT (-> k8s 'command')",
)
@click.option(
    "-i",
    "--interactive",
    "interactive",
    is_flag=True,
    help="accepted; stdin is not forwarded",
)
@click.option("-t", "--tty", "tty", is_flag=True)
@click.option(
    "--rm",
    "rm_flag",
    is_flag=True,
    help="accepted no-op; cleanup is already the default",
)
@click.option(
    "--pull", "pull", type=click.Choice(["always", "missing", "never"]), default=None
)
@click.option("--cpus", "cpus", default=None)
@click.option("-m", "--memory", "memory", default=None)
@click.option("--request-cpu", "request_cpu", default=None)
@click.option("--limit-cpu", "limit_cpu", default=None)
@click.option("--request-memory", "request_memory", default=None)
@click.option("--limit-memory", "limit_memory", default=None)
@click.option("--mode", "mode", type=click.Choice(["pod", "job"]), default=None)
@click.option("-n", "--namespace", "namespace", default=None)
@click.option(
    "--keep", "keep", is_flag=True, help="do not delete the pod/job after it finishes"
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, help="render the manifest without creating it"
)
@click.option(
    "--dry-run-server",
    "dry_run_server",
    is_flag=True,
    help="like --dry-run, but additionally validates the manifest against the apiserver",
)
@click.option("--timeout", "timeout", type=int, default=None)
@click.option("--quiet", "quiet", is_flag=True)
@click.option(
    "--no-color",
    "no_color",
    is_flag=True,
    help="disable colour/animation in squarepeg's own stderr output",
)
@click.option("--context", "context", default=None, help="kubeconfig context to use")
@click.option("--container-name", "container_name", default="main")
@click.argument("image")
@click.argument("command", nargs=-1, type=click.UNPROCESSED)
@_add_config_options
@_add_unsupported_options
def run(
    env_entries,
    volume_entries,
    name,
    workdir,
    entrypoint,
    interactive,
    tty,
    rm_flag,
    pull,
    cpus,
    memory,
    request_cpu,
    limit_cpu,
    request_memory,
    limit_memory,
    mode,
    namespace,
    keep,
    dry_run,
    dry_run_server,
    config_paths,
    no_default_config,
    profile,
    timeout,
    quiet,
    no_color,
    context,
    container_name,
    image,
    command,
):
    """Run IMAGE [COMMAND...] as a Kubernetes Pod or Job."""
    ui.set_color_override(False if no_color else None)
    ui.set_run_tag(None)
    check_supported_image(image)
    if rm_flag and keep:
        raise UsageError("--rm and --keep are mutually exclusive")
    if dry_run and dry_run_server:
        raise UsageError("--dry-run and --dry-run-server are mutually exclusive")

    resolved_config, _sources = _resolve_config(
        config_paths, no_default_config, profile
    )
    config_defaults = resolved_config.get("defaults") or {}
    config_volumes = resolved_config.get("volumes") or {}
    allow_host_path_mounts = coerce_bool(
        resolved_config.get("allow_host_path_mounts", False), "allow_host_path_mounts"
    )

    claims: set[str] = set()

    env = {**config_defaults.get("env", {}), **parse_env_entries(env_entries)}
    for key in parse_env_entries(env_entries):
        claims.add(f"/spec/containers/[name={container_name}]/env/[name={key}]")

    cli_volumes = [
        parse_volume_flag(
            v,
            config_volumes=config_volumes,
            allow_host_path_mounts=allow_host_path_mounts,
        )
        for v in volume_entries
    ]
    for vol in cli_volumes:
        claims.add(f"/spec/volumes/[name={vol.volume_name}]")
        claims.add(
            f"/spec/containers/[name={container_name}]/volumeMounts/[name={vol.volume_name}]"
        )

    # config-declared auto-mounts (volumes.NAME with a mount_path) are not claimed -- they're
    # config-driven, not CLI-driven, so kubernetes.spec passthrough in the same config can still
    # override them, same as any other config default (e.g. defaults.cpus).
    volumes = auto_mounts_from_config(config_volumes) + cli_volumes

    resources = ResourceSpec()
    cli_set_cpus, cli_set_memory = cpus is not None, memory is not None
    if cpus is None and "cpus" in config_defaults:
        cpus = config_defaults["cpus"]
    if memory is None and "memory" in config_defaults:
        memory = config_defaults["memory"]
    if cpus is not None:
        converted = docker_cpus_to_k8s(cpus)
        resources.request_cpu = converted
        resources.limit_cpu = converted
    if memory is not None:
        converted = docker_memory_to_k8s(memory)
        resources.request_memory = converted
        resources.limit_memory = converted
    if request_cpu is not None:
        resources.request_cpu = request_cpu
        claims.add(f"/spec/containers/[name={container_name}]/resources/requests/cpu")
    if limit_cpu is not None:
        resources.limit_cpu = limit_cpu
        claims.add(f"/spec/containers/[name={container_name}]/resources/limits/cpu")
    if request_memory is not None:
        resources.request_memory = request_memory
        claims.add(
            f"/spec/containers/[name={container_name}]/resources/requests/memory"
        )
    if limit_memory is not None:
        resources.limit_memory = limit_memory
        claims.add(f"/spec/containers/[name={container_name}]/resources/limits/memory")
    if cli_set_cpus and request_cpu is None:
        claims.add(f"/spec/containers/[name={container_name}]/resources/requests/cpu")
    if cli_set_cpus and limit_cpu is None:
        claims.add(f"/spec/containers/[name={container_name}]/resources/limits/cpu")
    if cli_set_memory and request_memory is None:
        claims.add(
            f"/spec/containers/[name={container_name}]/resources/requests/memory"
        )
    if cli_set_memory and limit_memory is None:
        claims.add(f"/spec/containers/[name={container_name}]/resources/limits/memory")

    if name is not None:
        validate_rfc1123(name, what="--name")
        claims.add("/metadata/name")
    else:
        name = generate_name(image)

    run_id = secrets.token_hex(4)
    ui.set_run_tag(run_id)

    if namespace is not None:
        claims.add("/metadata/namespace")
    else:
        namespace = resolved_config.get("namespace")

    pull_policy = (
        {"always": "Always", "missing": "IfNotPresent", "never": "Never"}.get(pull)
        if pull
        else None
    )
    if pull_policy is not None:
        claims.add(f"/spec/containers/[name={container_name}]/imagePullPolicy")
    elif "image_pull_policy" in config_defaults:
        pull_policy = config_defaults["image_pull_policy"]

    if workdir is not None:
        claims.add(f"/spec/containers/[name={container_name}]/workingDir")
    elif "workdir" in config_defaults:
        workdir = config_defaults["workdir"]

    mode = mode or resolved_config.get("mode", "pod")
    timeout = (
        timeout
        if timeout is not None
        else coerce_int(resolved_config.get("timeout", 300), "timeout")
    )
    quiet = quiet or coerce_bool(resolved_config.get("quiet", False), "quiet")
    cleanup = (
        False if keep else coerce_bool(resolved_config.get("cleanup", True), "cleanup")
    )
    orphan_sweep = coerce_bool(
        resolved_config.get("orphan_sweep", True), "orphan_sweep"
    )
    orphan_sweep_min_age = coerce_int(
        resolved_config.get("orphan_sweep_min_age", DEFAULT_MIN_AGE_SECONDS), "orphan_sweep_min_age"
    )
    if orphan_sweep_min_age < 0:
        raise UsageError(
            f"'orphan_sweep_min_age' must not be negative, got {orphan_sweep_min_age}"
        )

    entrypoint_tuple = (entrypoint,) if entrypoint is not None else None
    if entrypoint_tuple is not None:
        claims.add(f"/spec/containers/[name={container_name}]/command")
    if command:
        claims.add(f"/spec/containers/[name={container_name}]/args")

    if tty:
        claims.add(f"/spec/containers/[name={container_name}]/tty")
        if not interactive:
            # some clusters reject tty=true with stdin=false; -i is accepted but never
            # forwards real stdin, so this can hang a container that then blocks reading it.
            # This is a real hazard warning, so it's deliberately never suppressed by
            # --quiet (quiet=False below), unlike squarepeg's other informational chatter.
            chatter(
                "-t implies stdin is opened on the container (stdinOnce), but stdin is "
                "never actually forwarded; a process that blocks reading stdin will hang. Pass -i "
                "explicitly to acknowledge this.",
                quiet=False,
                level="warn",
            )
            interactive = True
    if interactive:
        claims.add(f"/spec/containers/[name={container_name}]/stdin")

    spec = RunSpec(
        image=image,
        args=tuple(command),
        entrypoint=entrypoint_tuple,
        env=env,
        workdir=workdir,
        volumes=volumes,
        tty=tty,
        stdin=interactive,
        pull_policy=pull_policy,
        mode=mode,
        namespace=namespace,
        name=name,
        container_name=container_name,
        resources=resources,
        cleanup=cleanup,
        timeout=timeout,
        quiet=quiet,
        orphan_sweep=orphan_sweep,
        orphan_sweep_min_age=orphan_sweep_min_age,
        claims=claims,
    )

    kubernetes_passthrough = resolved_config.get("kubernetes")
    job_passthrough = resolved_config.get("job")
    if spec.mode == "job":
        manifest = build_job(spec, kubernetes_passthrough, job_passthrough, run_id=run_id)
    else:
        manifest = build_pod(spec, kubernetes_passthrough, run_id=run_id)

    # kubernetes.metadata passthrough can legally overwrite the run-id label (it's not a
    # claimed field) -- if it did, the tag on screen must match what's actually on the
    # cluster, not the value we generated, so the printed tag is always kubectl -l ready.
    effective_run_id = manifest.get("metadata", {}).get("labels", {}).get(RUN_ID_LABEL)
    if effective_run_id and effective_run_id != run_id:
        chatter(
            f"config passthrough overrode the {RUN_ID_LABEL!r} label "
            f"(was {run_id!r}, now {effective_run_id!r})",
            level="warn",
        )
        ui.set_run_tag(effective_run_id)

    if dry_run_server:
        session = Session(namespace=spec.namespace, context=context, quiet=spec.quiet)
        spec.namespace = session.namespace
        validated = server_dry_run(session, spec, manifest)
        click.echo(yaml.safe_dump(validated, sort_keys=False), nl=False)
        return
    if dry_run:
        click.echo(yaml.safe_dump(manifest, sort_keys=False), nl=False)
        return

    session = Session(namespace=spec.namespace, context=context, quiet=spec.quiet)
    spec.namespace = session.namespace
    exit_code = run_manifest(session, spec, manifest)
    raise SystemExit(exit_code)


def _format_age(seconds: float) -> str:
    """kubectl-style compact duration: the two largest non-zero units, e.g. '10m12s',
    '3h14m', '2d4h'. Used only for --dry-run's human-readable listing."""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m{secs}s" if secs else f"{minutes}m"
    hours, mins = divmod(minutes, 60)
    if hours < 24:
        return f"{hours}h{mins}m" if mins else f"{hours}h"
    days, hrs = divmod(hours, 24)
    return f"{days}d{hrs}h" if hrs else f"{days}d"


def _candidate_detail(candidate) -> str:
    age = _format_age(candidate.age_seconds)
    if candidate.category == "terminal":
        return f"finished {age} ago"
    return f"{candidate.reason} for {age}"


@cli.command("clean")
@click.option("--dry-run", "dry_run", is_flag=True, help="list what would be deleted without deleting anything")
@click.option(
    "--min-age",
    "min_age",
    type=int,
    default=None,
    help="seconds a resource must be eligible before it's cleaned (default: config orphan_sweep_min_age, else 300)",
)
@click.option("-n", "--namespace", "namespace", default=None)
@click.option("--context", "context", default=None, help="kubeconfig context to use")
@click.option("--quiet", "quiet", is_flag=True)
@click.option(
    "--no-color",
    "no_color",
    is_flag=True,
    help="disable colour/animation in squarepeg's own stderr output",
)
@_add_config_options
def clean(dry_run, min_age, namespace, context, quiet, no_color, config_paths, no_default_config, profile):
    """Delete this user's own orphaned Pods/Jobs left behind by crashed or killed runs.

    Scoped to your own resources only (never a teammate's), and --keep'd resources are
    always exempt -- there is no flag to override either. Runs regardless of the
    'orphan_sweep' config key, which only affects the automatic per-run sweep. There is
    no confirmation prompt: the candidates are, by construction, either already finished
    or stuck and unable to start, so nothing running is ever at risk -- use --dry-run to
    preview what would be deleted first if you want to check.
    """
    ui.set_color_override(False if no_color else None)
    ui.set_run_tag(secrets.token_hex(4))

    resolved_config, _sources = _resolve_config(config_paths, no_default_config, profile)

    if namespace is None:
        namespace = resolved_config.get("namespace")
    quiet = quiet or coerce_bool(resolved_config.get("quiet", False), "quiet")

    if min_age is None:
        min_age = coerce_int(
            resolved_config.get("orphan_sweep_min_age", DEFAULT_MIN_AGE_SECONDS), "orphan_sweep_min_age"
        )
        if min_age < 0:
            raise UsageError(f"'orphan_sweep_min_age' must not be negative, got {min_age}")
    elif min_age < 0:
        raise UsageError(f"--min-age must not be negative, got {min_age}")

    session = Session(namespace=namespace, context=context, quiet=quiet)

    with ui.Status("scanning for orphaned resources", quiet=quiet):
        result = clean_orphans(session, min_age, quiet=quiet, dry_run=dry_run)

    if dry_run:
        if result.candidates:
            kind_name_width = max(len(f"{c.kind}/{c.name}") for c in result.candidates)
            category_width = max(len(c.category) for c in result.candidates)
            for c in result.candidates:
                kind_name = f"{c.kind}/{c.name}".ljust(kind_name_width)
                category = c.category.ljust(category_width)
                click.echo(f"{kind_name}  {category}  {_candidate_detail(c)}")
        return

    if result.failed:
        raise RunnerError(
            f"failed to delete {len(result.failed)} of {len(result.candidates)} orphaned resource(s)"
        )


def _print_config(config_paths, no_default_config, profile):
    ui.set_run_tag(None)  # config has no run context, even if invoked right after a 'run' in-process
    resolved_config, sources = _resolve_config(config_paths, no_default_config, profile)
    for path, origin in sources:
        chatter(f"{path} ({origin})", level="info")
    if not sources:
        chatter("no config files loaded", level="info")
    click.echo(yaml.safe_dump(resolved_config, sort_keys=False), nl=False)


@cli.group("config", invoke_without_command=True)
@_add_config_options
@click.pass_context
def config_group(ctx, config_paths, no_default_config, profile):
    """Inspect the resolved config (same as 'config show')."""
    if ctx.invoked_subcommand is None:
        _print_config(config_paths, no_default_config, profile)
        return
    default_map = {}
    if config_paths:
        default_map["config_paths"] = config_paths
    if no_default_config:
        default_map["no_default_config"] = no_default_config
    if profile is not None:
        default_map["profile"] = profile
    ctx.default_map = {"show": default_map}


@config_group.command("show")
@_add_config_options
def config_show(config_paths, no_default_config, profile):
    """Print the merged effective config (stdout) and its sources (stderr). Same as bare 'config'."""
    _print_config(config_paths, no_default_config, profile)


def main():
    try:
        cli(standalone_mode=False)
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(exc.exit_code) from exc
    except click.exceptions.Exit as exc:
        raise SystemExit(exc.exit_code) from exc
    except SquarepegError as exc:
        click.echo(
            click.style(f"squarepeg: {exc}", fg="red", bold=True),
            err=True,
            color=ui.color_enabled(),
        )
        raise SystemExit(exc.exit_code) from exc


if __name__ == "__main__":
    main()

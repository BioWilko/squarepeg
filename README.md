# squarepeg

Run a `docker run`-style command as a Kubernetes Pod or Job, streaming its
output like a local process and exiting with the container's exit code.

## Usage

```
squarepeg run [FLAGS] IMAGE [COMMAND...]
```

Common flags: `-e/--env`, `-v/--volume`, `--name`, `-w/--workdir`,
`--entrypoint`, `-i/-t`, `--pull`, `--cpus`, `--memory/-m`,
`--request-cpu/--limit-cpu/--request-memory/--limit-memory`, `--mode pod|job`,
`-n/--namespace`, `--keep`, `--dry-run`, `--config` (repeatable),
`--no-default-config`, `--profile`, `--timeout`, `--context`, `--quiet`.

```
squarepeg config show [--config PATH]... [--no-default-config] [--profile NAME]
```

Prints the merged effective config (stdout) and the files it came from, in
order (stderr).

## Config

Config is layered Nextflow-style, lowest to highest precedence:

1. built-in defaults
2. the single default file `~/.config/squarepeg/config.yaml`, if present
3. `$SQUAREPEG_CONFIG` — a colon-separated list of paths, left to right
4. each `--config PATH` given on the CLI, in order, left to right
5. individual CLI flags

Every layer is deep-merged key by key (not replaced wholesale), so a later
layer can override just one setting without restating everything else. Use
`--no-default-config` for a hermetic, reproducible resolution.

See the schema and an example in [`squarepeg/config.py`](squarepeg/config.py)
(`ALLOWED_TOP_KEYS`, `ALLOWED_DEFAULTS_KEYS`) — top-level keys are
`namespace`, `mode`, `cleanup`, `timeout`, `quiet`, `allow_host_path_mounts`,
`split_streams`, `defaults`, `volumes`, `kubernetes`, `job`, `profiles`. The
`kubernetes` and `job` sections accept arbitrary Kubernetes fields verbatim
(deep-merged into the generated manifest) and are not validated by squarepeg
itself — use `--dry-run` to inspect the result. `kubernetes.spec` is always a
**pod** spec, even in `--mode job`; squarepeg re-homes it under the Job's pod
template so the same config works in either mode.

## Divergences from `docker run`

| docker behaviour | squarepeg behaviour | why |
|---|---|---|
| host bind mounts (`-v /host:/container`) | rejected by default | there is no "host" on a remote cluster; use a config-defined named volume backed by a PVC, or set `allow_host_path_mounts: true` for a local cluster |
| named volumes persist between runs | named volumes fall back to `emptyDir` unless declared in config's `volumes:` section | an anonymous or unconfigured volume is scratch space only |
| stdout/stderr can be split | merged into one stream by default | splitting is opt-in (`--split-streams`) and depends on a cluster feature gate |
| `-i` forwards your stdin | accepted but stdin is **not** forwarded | the log endpoint is read-only; real interactive attach is not implemented |
| `-t` alone works | `-t` without `-i` implies stdin is opened on the container anyway (some clusters reject `tty` without `stdin`) | a process that then blocks reading stdin will hang; squarepeg warns when this happens |
| `-m 512m` means 512 MiB | converted to the equivalent k8s quantity (`512Mi`) | a bare k8s `m` suffix means *milli*, not mebi; passing docker units through unconverted would silently produce a near-zero memory limit |
| container exits 137/OOM | reported as-is, with the `OOMKilled` reason printed for clarity | a bare 137 is opaque |
| detached mode (`-d`), networking (`--network`, `-p`), etc. | rejected with a message pointing at the config passthrough equivalent, if one exists | these are host/daemon concepts with no remote-cluster equivalent |

Unsupported flags fail fast with a specific message rather than being
silently ignored.

## Development

```
pip install -e ".[dev]"
pytest                        # unit tests
pytest -m integration         # integration tests, requires SQUAREPEG_INTEGRATION=1 and a kind cluster
ruff check .                  # lint
```

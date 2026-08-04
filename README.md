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

Prints the merged, **resolved** (post-`${VAR}` interpolation) effective
config (stdout) and the files it came from, in order (stderr).

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

See [`examples/config.yaml`](examples/config.yaml) for a fully worked example
covering every section, or the schema itself in
[`squarepeg/config.py`](squarepeg/config.py) (`ALLOWED_TOP_KEYS`,
`ALLOWED_DEFAULTS_KEYS`) — top-level keys are `namespace`, `mode`, `cleanup`,
`timeout`, `quiet`, `allow_host_path_mounts`, `split_streams`, `defaults`,
`volumes`, `kubernetes`, `job`, `profiles`. The `kubernetes` and `job`
sections accept arbitrary Kubernetes fields verbatim (deep-merged into the
generated manifest) and are not validated by squarepeg itself — use
`--dry-run` to inspect the result. `kubernetes.spec` is always a **pod**
spec, even in `--mode job`; squarepeg re-homes it under the Job's pod
template so the same config works in either mode.

### Environment variable references

Any string value anywhere in a config file — including inside the
unvalidated `kubernetes`/`job` passthrough sections — can reference an
environment variable:

- `${VAR}` — substitutes `$VAR`; a hard error naming the file and the
  variable if it's not set.
- `${VAR:-default}` — substitutes `$VAR` if it is set **and non-empty**,
  else the literal `default`. This is bash's `:-` operator: a variable set
  to the empty string counts as unset, which is easy to forget.
- `$$` — a literal `$`. Bare `$VAR` (no braces) is not a reference and is
  left untouched.

A default is always a literal — it is never itself re-scanned for further
references, and a value that happens to *contain* `${...}` text is never
re-expanded.

Substitution is **eager**: every file is resolved in full as soon as it's
loaded, including profile bodies you didn't select with `--profile`. This
matches how squarepeg already validates every profile regardless of
selection — a typo in an unused profile is a hard error either way.

Substitution always produces a string. That's fine for squarepeg's own
string-typed settings (`namespace`, `defaults.cpus`, etc. — `timeout`,
`quiet`, `cleanup` and `allow_host_path_mounts` are explicitly coerced from
a string if needed), but an integer/boolean field inside the `kubernetes`/
`job` passthrough (e.g. `backoffLimit`, `readOnly`) must not be interpolated
into a quoted string — the apiserver will reject it, and the error will be
visible immediately via `--dry-run`.

Because `squarepeg config show` and `--dry-run` print **resolved** values,
anything pulled from a secret-bearing environment variable will appear in
their output — treat that output the same way you'd treat a shell history
containing secrets. squarepeg does not redact anything; for real secrets,
prefer referencing a Kubernetes Secret directly in `kubernetes.spec` (e.g.
via `envFrom`/`secretKeyRef`), which never passes through squarepeg's own
process at all.

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

# squarepeg

Run a `docker run`-style command as a Kubernetes Pod or Job, streaming its
output like a local process and exiting with the container's exit code.

```
squarepeg run alpine echo "hello from a pod"
```

## Install

```
pip install -e ".[dev]"
```

## Docs

Full documentation lives in the [wiki](https://github.com/BioWilko/squarepeg/wiki):

- [Getting Started](https://github.com/BioWilko/squarepeg/wiki/Getting-Started)
- [CLI Reference](https://github.com/BioWilko/squarepeg/wiki/CLI-Reference)
- [Configuration](https://github.com/BioWilko/squarepeg/wiki/Configuration)
- [Environment Variable Interpolation](https://github.com/BioWilko/squarepeg/wiki/Environment-Variable-Interpolation)
- [Docker Compatibility](https://github.com/BioWilko/squarepeg/wiki/Docker-Compatibility)
- [Execution Lifecycle](https://github.com/BioWilko/squarepeg/wiki/Execution-Lifecycle)
- [Troubleshooting](https://github.com/BioWilko/squarepeg/wiki/Troubleshooting)
- [Development](https://github.com/BioWilko/squarepeg/wiki/Development)

See also [`examples/config.yaml`](examples/config.yaml) for a fully worked
config file.

## Development

```
pytest                        # unit tests
pytest -m integration         # integration tests, requires SQUAREPEG_INTEGRATION=1 and a kind cluster
ruff check .                  # lint
```

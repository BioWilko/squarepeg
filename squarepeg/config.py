"""Nextflow-style layered config loading.

Resolution order, lowest to highest precedence:
  1. built-in defaults (not a file; handled by callers)
  2. the single default path ~/.config/squarepeg/config.yaml, if present
  3. $SQUAREPEG_CONFIG: a colon-separated list of paths, left-to-right
  4. each --config PATH given on the CLI, in order, left-to-right
  5. individual CLI flags (applied by callers, not here)

Every file in 2-4 is merged with the same key-level deep_merge used for k8s
passthrough (with an empty claim set) -- see merge.py for why whole-file
override would defeat the point of layering.
"""

import os
from pathlib import Path

import yaml

from squarepeg.errors import ConfigError
from squarepeg.merge import deep_merge

ENV_VAR = "SQUAREPEG_CONFIG"
DEFAULT_CONFIG_PATH = Path.home() / ".config" / "squarepeg" / "config.yaml"

ALLOWED_TOP_KEYS = {
    "namespace",
    "mode",
    "cleanup",
    "timeout",
    "quiet",
    "allow_host_path_mounts",
    "split_streams",
    "defaults",
    "volumes",
    "kubernetes",
    "job",
    "profiles",
}
ALLOWED_DEFAULTS_KEYS = {"cpus", "memory", "image_pull_policy", "workdir", "env"}


def _resolve_env_paths() -> list[tuple[Path, str]]:
    raw = os.environ.get(ENV_VAR, "")
    resolved = []
    for segment in raw.split(":"):
        if not segment:
            continue
        path = Path(segment).expanduser()
        if not path.exists():
            raise ConfigError(f"config file {path} (from ${ENV_VAR}) does not exist")
        resolved.append((path, f"${ENV_VAR}"))
    return resolved


def _resolve_cli_paths(cli_config_paths: tuple[str, ...]) -> list[tuple[Path, str]]:
    resolved = []
    for raw in cli_config_paths:
        path = Path(raw).expanduser()
        if not path.exists():
            raise ConfigError(f"config file {path} (from --config {raw}) does not exist")
        resolved.append((path, f"--config {raw}"))
    return resolved


def resolve_config_sources(
    cli_config_paths: tuple[str, ...] = (), *, no_default_config: bool = False
) -> list[tuple[Path, str]]:
    """Return [(path, origin_label)] in the order they should be merged, lowest precedence first."""
    sources: list[tuple[Path, str]] = []
    if not no_default_config and DEFAULT_CONFIG_PATH.exists():
        sources.append((DEFAULT_CONFIG_PATH, "default location"))
    sources.extend(_resolve_env_paths())
    sources.extend(_resolve_cli_paths(cli_config_paths))
    return sources


def load_yaml_document(path: Path) -> dict:
    try:
        text = path.read_text()
    except OSError as exc:
        raise ConfigError(f"cannot read config file {path}: {exc}") from exc
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"malformed YAML in {path}: {exc}") from exc
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ConfigError(f"config file {path} must be a YAML mapping at the top level")
    return data


def validate_document(doc: dict, source: str, *, allow_profiles: bool = True) -> None:
    for key in doc:
        if key not in ALLOWED_TOP_KEYS:
            raise ConfigError(f"unknown config key {key!r} in {source}")
    defaults = doc.get("defaults")
    if defaults:
        for key in defaults:
            if key not in ALLOWED_DEFAULTS_KEYS:
                raise ConfigError(f"unknown key 'defaults.{key}' in {source}")
    profiles = doc.get("profiles")
    if profiles:
        if not allow_profiles:
            raise ConfigError(f"nested 'profiles' is not allowed inside a profile (in {source})")
        for name, body in profiles.items():
            if not isinstance(body, dict):
                raise ConfigError(f"profile {name!r} in {source} must be a mapping")
            validate_document(body, f"{source} (profile {name!r})", allow_profiles=False)


def load_effective_config(
    cli_config_paths: tuple[str, ...] = (), *, no_default_config: bool = False
) -> tuple[dict, list[tuple[Path, str]]]:
    """Load and merge every config layer. Returns (effective_config, sources)."""
    sources = resolve_config_sources(cli_config_paths, no_default_config=no_default_config)
    effective: dict = {}
    for path, origin in sources:
        doc = load_yaml_document(path)
        validate_document(doc, f"{path} ({origin})")
        effective = deep_merge(effective, doc)
    return effective, sources


def select_profile(effective_config: dict, profile_name: str | None) -> dict:
    """Fold the named profile over the rest of the effective config (L1+L2)."""
    if profile_name is None:
        return {k: v for k, v in effective_config.items() if k != "profiles"}
    profiles = effective_config.get("profiles") or {}
    if profile_name not in profiles:
        available = ", ".join(sorted(profiles)) or "(none defined)"
        raise ConfigError(f"unknown profile {profile_name!r}; available profiles: {available}")
    base = {k: v for k, v in effective_config.items() if k != "profiles"}
    return deep_merge(base, profiles[profile_name])

"""Substitute ${VAR} / ${VAR:-default} references in config YAML values.

Runs per-file, before validation and before merging -- so error messages can
name the exact source file, and everything downstream (validation, merging,
manifest building) only ever sees fully-resolved strings. Interpolation is
eager: every string leaf in the parsed document is walked, including inside
profile bodies that are not selected on this invocation (this matches
validate_document, which already validates every profile regardless of
selection) and inside the free-form 'kubernetes'/'job' passthrough sections
(interpolation is structural and doesn't care that those sections are
otherwise unvalidated).

Supported forms:
  ${VAR}             -- substitutes $VAR; a ConfigError if VAR is unset
  ${VAR:-default}    -- substitutes $VAR if set AND non-empty, else the
                         literal 'default' (bash ':-' semantics: an
                         empty-string value counts as unset)
  $$                 -- a literal '$'

Only ${...} references are recognised -- bare $VAR (no braces) is not a
reference and passes through untouched. A default is a literal: it is never
itself re-scanned for further references. Anything else starting with '${'
that isn't a well-formed reference (${}, ${1BAD}, ${A B}, an unterminated
${VAR) is a hard error, consistent with this tool's fail-fast posture
elsewhere (missing config files, unknown config keys, unsupported flags).

Mapping keys are never interpolated, only values.
"""

import os
import re
from collections.abc import Mapping
from typing import Any

from squarepeg.errors import ConfigError

_TOKEN_RE = re.compile(
    r"""
      (?P<escape>\$\$)
    | \$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}
    | (?P<bad>\$\{[^}]*\}?)
    """,
    re.VERBOSE,
)


def interpolate_string(value: str, *, source: str, path: str, env: Mapping[str, str]) -> str:
    def replace(match: re.Match) -> str:
        if match.group("escape") is not None:
            return "$"
        if match.group("bad") is not None:
            raise ConfigError(
                f"malformed reference {match.group('bad')!r} in {path!r} in {source}; "
                "expected ${VAR} or ${VAR:-default}"
            )
        name = match.group("name")
        default = match.group("default")
        if default is not None:
            resolved = env.get(name)
            return resolved if resolved else default
        try:
            return env[name]
        except KeyError:
            raise ConfigError(
                f"environment variable {name!r} is not set, referenced by {path!r} in {source}. "
                f"Use ${{{name}:-default}} to supply a fallback."
            ) from None

    return _TOKEN_RE.sub(replace, value)


def interpolate_value(value: Any, *, source: str, path: str, env: Mapping[str, str]) -> Any:
    if isinstance(value, str):
        return interpolate_string(value, source=source, path=path, env=env)
    if isinstance(value, Mapping):
        return {
            key: interpolate_value(child, source=source, path=f"{path}.{key}" if path else str(key), env=env)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [
            interpolate_value(child, source=source, path=f"{path}[{i}]", env=env) for i, child in enumerate(value)
        ]
    return value


def interpolate_document(doc: dict, source: str, env: Mapping[str, str] | None = None) -> dict:
    """Walk a parsed config document, substituting ${VAR}/${VAR:-default} in every string leaf."""
    return interpolate_value(doc, source=source, path="", env=os.environ if env is None else env)

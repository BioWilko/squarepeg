"""A single deep-merge routine used for three distinct layers:

- folding multiple config file layers together (empty claim set)
- applying a selected profile over the base config (empty claim set)
- overlaying k8s passthrough config onto a generated manifest, where CLI-set
  paths ("claims") must not be touched (see runspec.RunSpec.claims)

Dicts merge key by key. Lists of dicts that all carry a 'name' key merge by
name (this is what makes 'containers', 'env', 'volumes' etc. behave
intuitively); any other list is replaced wholesale by the overlay, which is
also required so a later layer can remove an inherited entry (e.g.
'tolerations: []') rather than only being able to append to it.
"""

from collections.abc import Mapping, Sequence
from typing import Any


def _is_named_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, Mapping) and "name" in item for item in value)


def _merge_named_lists(base: list, overlay: list, claims: frozenset, path: str) -> list:
    by_name = {item["name"]: item for item in base}
    order = [item["name"] for item in base]
    for item in overlay:
        name = item["name"]
        child_path = f"{path}/[name={name}]"
        if name in by_name:
            by_name[name] = deep_merge(by_name[name], item, claims, child_path)
        else:
            by_name[name] = item
            order.append(name)
    return [by_name[name] for name in order]


def deep_merge(base: Any, overlay: Any, claims: frozenset[str] = frozenset(), path: str = "") -> Any:
    if path in claims:
        return base

    if isinstance(base, Mapping) and isinstance(overlay, Mapping):
        result = dict(base)
        for key, value in overlay.items():
            child_path = f"{path}/{key}"
            result[key] = deep_merge(result[key], value, claims, child_path) if key in result else value
        return result

    if isinstance(base, list) and isinstance(overlay, Sequence) and not isinstance(overlay, (str, bytes)):
        overlay = list(overlay)
        if _is_named_list(base) and _is_named_list(overlay):
            return _merge_named_lists(base, overlay, claims, path)
        return overlay

    return overlay

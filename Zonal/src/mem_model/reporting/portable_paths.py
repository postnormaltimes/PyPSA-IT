"""Resolve receipt paths without rewriting preserved historical evidence."""
from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath

from ..common import ROOT, sha256_file


def receipt_path(name, root_map=None, *, root=None):
    """Map an explicitly configured historical root to its relocated copy.

    ``MEM_REPORTING_PATH_MAP`` is a JSON object of historical roots to current
    directories. Configured destinations may be relative to the project root.
    Longest matching roots win; there is no filename search or legacy fallback.
    """
    root = Path(root or ROOT)
    mapping = dict(root_map or {})
    override = os.environ.get("MEM_REPORTING_PATH_MAP")
    if override:
        mapping.update(json.loads(override))
    normalized = str(name).replace("\\", "/")
    windows = PureWindowsPath(normalized).is_absolute()
    compare = normalized.casefold() if windows else normalized
    for original, destination in sorted(mapping.items(), key=lambda item: len(str(item[0])), reverse=True):
        prefix = str(original).replace("\\", "/").rstrip("/")
        key = prefix.casefold() if windows else prefix
        if compare == key or compare.startswith(key + "/"):
            tail = normalized[len(prefix):].lstrip("/")
            if ".." in Path(tail).parts:
                raise RuntimeError("REPORTING_PATH_MAP_ESCAPE")
            target = Path(destination)
            target = target if target.is_absolute() else root / target
            return target / tail
    path = Path(normalized)
    if windows and os.name != "nt":
        raise RuntimeError(f"REPORTING_HISTORICAL_ROOT_MAP_REQUIRED: {name}")
    if path.is_absolute():
        if not path.resolve().is_relative_to(root.resolve()):
            raise RuntimeError(f"REPORTING_HISTORICAL_ROOT_MAP_REQUIRED: {name}")
        return path
    if ".." in path.parts:
        raise RuntimeError("REPORTING_PATH_MAP_ESCAPE")
    return root / path


def portable_name(path, *, root=None):
    path, root = Path(path).resolve(), Path(root or ROOT).resolve()
    return path.relative_to(root).as_posix() if path.is_relative_to(root) else str(path)


def verify_pins(pins, root_map=None, *, root=None):
    for name, digest in pins.items():
        path = receipt_path(name, root_map, root=root)
        if not path.is_file() or sha256_file(path) != digest:
            raise RuntimeError(f"MARGINAL_REGIME_HASH_CONFLICT: {path}")

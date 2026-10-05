"""Shared deterministic artifact and receipt helpers for manual Stage-A gates."""

from __future__ import annotations

import hashlib
import json
import platform
import shlex
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import pandas as pd


ROOT = Path(__file__).resolve().parents[3]
REQUIRED_RECEIPT_FIELDS = (
    "phase",
    "gate",
    "status",
    "timestamp",
    "python_version",
    "pypsa_version",
    "linopy_version",
    "solver",
    "solver_version",
    "input_manifests",
    "command",
    "outputs",
    "qa",
    "next_gate",
)


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest().upper()


def relative_path(path: Path) -> str:
    resolved = path.resolve()
    try:
        return resolved.relative_to(ROOT).as_posix()
    except ValueError:
        return resolved.as_posix()


def runtime_versions() -> dict[str, str]:
    import linopy
    import pypsa

    try:
        import gurobipy as gp

        solver_version = ".".join(str(part) for part in gp.gurobi.version())
    except ImportError:
        solver_version = "NOT_INSTALLED"
    return {
        "python_version": platform.python_version(),
        "pypsa_version": pypsa.__version__,
        "linopy_version": linopy.__version__,
        "solver_version": solver_version,
    }


def command_string(
    argv: Iterable[str] | None = None,
    *,
    module: str | None = None,
) -> str:
    arguments = list(sys.argv[1:]) if argv is None else list(argv)
    values = [sys.executable]
    if module:
        values.extend(["-m", module])
    values.extend(arguments)
    return shlex.join(values)


def build_receipt(
    *,
    phase: str,
    gate: str,
    status: str,
    input_manifests: list[dict[str, Any]],
    outputs: dict[str, Any],
    qa: dict[str, Any],
    next_gate: str,
    command: str | None = None,
    runtime_seconds: float | None = None,
    solve: dict[str, Any] | None = None,
) -> dict[str, Any]:
    versions = runtime_versions()
    receipt: dict[str, Any] = {
        "phase": phase,
        "gate": gate,
        "status": status,
        "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        **versions,
        "solver": "gurobi",
        "input_manifests": input_manifests,
        "command": command or command_string(),
        "outputs": outputs,
        "qa": qa,
        "next_gate": next_gate,
    }
    if runtime_seconds is not None:
        receipt["runtime_seconds"] = float(runtime_seconds)
    if solve:
        receipt.update(solve)
    validate_receipt(receipt)
    return receipt


def validate_receipt(receipt: dict[str, Any]) -> None:
    missing = [field for field in REQUIRED_RECEIPT_FIELDS if field not in receipt]
    if missing:
        raise ValueError(f"Receipt is missing required fields: {missing}")
    if receipt["status"] not in {"PASS", "FAIL", "STOP", "PARTIAL"}:
        raise ValueError(f"Unsupported receipt status: {receipt['status']}")
    if not isinstance(receipt["input_manifests"], list):
        raise TypeError("input_manifests must be a list")
    if not isinstance(receipt["outputs"], dict) or not isinstance(receipt["qa"], dict):
        raise TypeError("outputs and qa must be objects")


def write_receipt(path: Path, receipt: dict[str, Any]) -> Path:
    validate_receipt(receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def read_receipt(path: Path, *, required_status: str = "PASS") -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Required predecessor receipt is missing: {path}")
    receipt = json.loads(path.read_text(encoding="utf-8"))
    validate_receipt(receipt)
    if receipt["status"] != required_status:
        raise RuntimeError(f"Required predecessor receipt is not {required_status}: {path}")
    return receipt


def _row_count(path: Path) -> int | None:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return len(pd.read_csv(path))
    if suffix in {".parquet", ".pq"}:
        return len(pd.read_parquet(path))
    return None


def write_manifest(path: Path, artifacts: Iterable[Path]) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for artifact in sorted((item.resolve() for item in artifacts), key=lambda item: relative_path(item)):
        if not artifact.exists():
            raise FileNotFoundError(artifact)
        rows.append(
            {
                "relative_path": relative_path(artifact),
                "bytes": artifact.stat().st_size,
                "rows": _row_count(artifact),
                "sha256": sha256_file(artifact),
                "status": "AUTHORITATIVE_PHASE_OUTPUT",
            }
        )
    frame = pd.DataFrame.from_records(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return frame

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml


ROOT = Path(__file__).resolve().parents[2]
WORKSPACE = ROOT.parent
STATIC = ROOT / "pre_pypsa_inputs"
CONFIG = ROOT / "config"
DECISIONS = ROOT / "decision_packets"
RUNTIME = ROOT / "runtime_inputs"
ACCEPTED_RUNTIME = RUNTIME / "accepted"
ACCEPTED_RUNTIME_2050 = RUNTIME / "accepted_2050"
FIXTURE_RUNTIME = RUNTIME / "synthetic_fixture_not_model_input"
QA = ROOT / "qa" / "interim"
NETWORKS = ROOT / "networks" / "unsolved"
SMOKE_RESULTS = ROOT / "results" / "smoke"

ZONES = ("NORD", "CNOR", "CSUD", "SUD", "CALA", "SICI", "SARD")
SCENARIOS = (
    (2040, "Slow"),
    (2040, "Base"),
    (2040, "High"),
    (2050, "Slow"),
    (2050, "Base"),
    (2050, "High"),
)
PRICE_MARKETS = ("FR", "CH", "AT", "SI", "ME", "GR", "TN", "MT")


def ensure_output_dirs() -> None:
    for path in (DECISIONS, ACCEPTED_RUNTIME, FIXTURE_RUNTIME, QA, NETWORKS, SMOKE_RESULTS):
        path.mkdir(parents=True, exist_ok=True)


def load_yaml(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle)


def dump_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if value is None or pd.isna(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y"}


def scenario_key(year: int, scenario: str) -> str:
    return f"{int(year)}_{str(scenario).title()}"


def safe_id(value: object) -> str:
    text = str(value).strip().upper()
    for source, replacement in ((" ", "_"), ("/", "_"), ("+", "PLUS"), ("-", "_")):
        text = text.replace(source, replacement)
    return "".join(character for character in text if character.isalnum() or character == "_")


def assert_gate(name: str) -> str:
    gates = load_yaml(CONFIG / "approval_gates.yaml")
    gate = gates[name]
    approved_id = gate.get("approved_id")
    if gate.get("status") != "APPROVED" or not approved_id:
        raise RuntimeError(
            f"{name} gate is not approved. Review decision_packets and set status=APPROVED "
            "with an approved_id only after explicit user approval."
        )
    return str(approved_id)


def read_runtime_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    if path.suffix.lower() == ".parquet":
        return pd.read_parquet(path)
    return pd.read_csv(path)

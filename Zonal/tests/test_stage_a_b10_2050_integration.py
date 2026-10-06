"""No-solve checks for the incoming B9H package and horizon-scoped B10 CLI."""

import json
from pathlib import Path

import pandas as pd
import pytest
import yaml

from mem_model.stage_a import execution
from mem_model.stage_a.execution import build_parser
from mem_model.stage_a.receipts import sha256_file


ROOT = Path(__file__).resolve().parents[1]
B9H = ROOT / "qa/stage_a/etx7b9h/MEM_ETX7B9H_Run_Receipt_v1.0.json"
RESULTS = ROOT / "stage_a_results/2050_perimeter_closure_final_mt_tn"
STEM = "MEM_ETX7B9H_2050_FINAL_RESIDUAL_MT_TN_CLOSURE"
MANIFEST = RESULTS / f"{STEM}_Result_Manifest_v1.0.csv"
PRICES = RESULTS / f"{STEM}_Market_Prices.parquet"
NETWORK = RESULTS / f"{STEM}_SOLVED.nc"


def test_b9h_incoming_bytes_match_reviewed_controls() -> None:
    assert sha256_file(B9H) == "B95CB4B4FB59530872EF706185A09840B5EAB4C40AD565F1C6B31BA341E8906D"
    assert sha256_file(MANIFEST) == "07D33D8A9EEA044FF09AD8D9802716730F79EB5887BC0330BC35A937E6A10FB8"
    assert sha256_file(PRICES) == "57A4D5AFFA906B14FA7245D28972F9C53DEDA51E5D66CDA2782723419B33DED7"
    assert sha256_file(NETWORK) == "ADE607FB4D853A1DBD1CB8004B382927174ACE6507FB87129BD4ABA65000362C"


def test_b9h_result_manifest_members_are_intact() -> None:
    manifest = pd.read_csv(MANIFEST)
    assert len(manifest) == 27
    assert not manifest["relative_path"].duplicated().any()
    assert all(
        (ROOT / row.relative_path).is_file()
        and sha256_file(ROOT / row.relative_path) == row.sha256
        for row in manifest.itertuples()
    )


def test_b10_2050_cli_is_distinct_from_2040() -> None:
    parser = build_parser()
    args = parser.parse_args(["b10-2050"])
    assert args.command == "b10-2050"
    assert args.execute is False


def test_b10_2050_rejects_a_non_b9h_source_even_if_approved(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    sources = yaml.safe_load((ROOT / "config/stage_a_production_price_sources.yaml").read_text(encoding="utf-8"))
    approvals = yaml.safe_load((ROOT / "config/approval_gates.yaml").read_text(encoding="utf-8"))
    sources["horizon_governance"][2050] = {"production_source_resolved": True, "b10_authorized": True}
    approvals["stage_a_manual_gates"]["b10_2050_authorized"] = True
    sources["sources"][2050]["status"] = "ACCEPTED_PRODUCTION_PRICE_SOURCE"
    sources["sources"][2050]["phase"] = "ETX-7B9G"
    source_path = tmp_path / "sources.yaml"
    gates_path = tmp_path / "gates.yaml"
    source_path.write_text(yaml.safe_dump(sources), encoding="utf-8")
    gates_path.write_text(yaml.safe_dump(approvals), encoding="utf-8")
    monkeypatch.setattr(execution, "PRODUCTION_PRICE_SOURCE_CONFIG", source_path)
    monkeypatch.setattr(execution, "APPROVAL_GATES_CONFIG", gates_path)
    with pytest.raises(RuntimeError, match="ONLY_B9H_ACCEPTED_SOURCE"):
        execution._load_accepted_production_price_sources((2050,))


def test_b9h_receipt_preserves_b9g_parent_controls_without_runtime_dependency() -> None:
    receipt = json.loads(B9H.read_text(encoding="utf-8"))
    controls = {row.get("input_id"): row for row in receipt["input_manifests"]}
    assert controls["B9G_IMMUTABLE_TECHNICALLY_VALID_PARENT"]["observed_sha256"] == (
        "384A79C03289E0D612FAE1568B3271D9BB15F00603F4719A4A2AA1C5337276CF"
    )
    assert controls["B9G_RESULT_MANIFEST"]["observed_sha256"] == (
        "FCBD33F69A716D34FE898EC3BB478B24E7AE3E67D19A333DD9F3EC4FEB91BB9C"
    )
    assert controls["B9G_SOLVED_NETWORK"]["observed_sha256"] == (
        "C24AD78015BD203A1AE7C07DD6EDCA90321BCA73556193C83DC7371A6D6D2011"
    )

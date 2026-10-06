from __future__ import annotations

from pathlib import Path

import yaml

from mem_model.stage_a.etx7a import (
    COUNTRIES,
    EXPECTED_ROLE_COUNTS,
    RUNTIME_ROLES,
    read_canonical_evidence,
    read_runtime_inputs,
    verify_frozen_sources,
)


ROOT = Path(__file__).resolve().parents[1]


def test_frozen_source_hashes_match_manifest() -> None:
    receipt = verify_frozen_sources()
    assert len(receipt) == 2
    assert set(receipt["status"]) == {"PASS"}
    assert set(receipt["role"]) == {"FROZEN_STATIC_AUTHORITY"}


def test_etx7a_rows_roles_and_country_domain() -> None:
    canonical = read_canonical_evidence()
    runtime = read_runtime_inputs()
    assert len(canonical) == 207
    assert len(runtime) == 161
    assert runtime["runtime_role"].value_counts().to_dict() == EXPECTED_ROLE_COUNTS
    assert set(runtime["runtime_role"]) == set(RUNTIME_ROLES)
    assert set(runtime["country_code"]) == set(COUNTRIES)
    assert runtime["canonical_etx7a_workbook_row"].notna().all()


def test_runtime_sheet_preserves_legitimate_repeated_harmonised_groups() -> None:
    runtime = read_runtime_inputs()
    repeated = runtime.groupby(["country_code", "harmonised_group", "unit"]).size()
    observed = set(repeated[repeated.gt(1)].index)
    assert observed == {
        ("AT", "hydro_non_phs", "GW"),
        ("CH", "hydro_non_phs", "GW"),
        ("CH", "waste_chp", "GW"),
    }


def test_stage_a_config_matches_frozen_ingestion_contract() -> None:
    config = yaml.safe_load(
        (ROOT / "config" / "stage_a.yaml").read_text(encoding="utf-8")
    )
    assert config["contract_version"] == "ETX7A_V1_0"
    assert set(config["runtime_roles"]) == set(RUNTIME_ROLES)
    assert set(config["countries"]) == set(COUNTRIES)
    assert config["horizons"] == [2040, 2050]
    assert config["approved_architecture"]["stage_a_stage_b_separation"] is True
    # The later accepted fixed-ten-market revision supersedes the earlier wider
    # Mediterranean-context candidate without altering the ETX-7A static rows.
    assert config["approved_architecture"]["connected_mediterranean_context"] is False
    assert config["approved_architecture"]["isolate_tunisia_to_italy_only"] is True
    assert "NA_CONTEXT_2019_PROFILE_METHOD_PENDING" not in config["pending_runtime_items"]
    assert "PYPSA_NETWORK_CONSTRUCTION" in config["scope_excluded"]

from __future__ import annotations

import itertools
from pathlib import Path

import pandas as pd

from mem_model.stage_a.topology import (
    DEFAULT_OUTPUT_DIR,
    MARKETS,
    OUTPUT_FILES,
    build_topology_package,
)
from mem_model.stage_a.topology_validation import validate_topology_package


def _read(key: str, directory: Path = DEFAULT_OUTPUT_DIR) -> pd.DataFrame:
    return pd.read_csv(directory / OUTPUT_FILES[key], dtype=str, keep_default_na=False)


def test_exact_ten_node_scope_and_explicit_exclusions() -> None:
    nodes = _read("nodes")
    excluded = _read("excluded_markets")

    assert len(nodes) == 10
    assert nodes.market.is_unique
    assert set(nodes.market) == set(MARKETS)
    assert nodes.capacity_expansion_allowed.str.lower().eq("false").all()
    assert set(excluded.market) == {
        "CORS", "DE-LU", "BE", "ES", "GB", "CZ", "SK", "HU",
        "BA", "RS", "AL", "MK", "BG", "TR", "DZ", "LY",
    }
    assert set(nodes.market).isdisjoint(excluded.market)


def test_directional_link_registry_is_complete_fixed_and_unambiguous() -> None:
    links = _read("links")
    interfaces = _read("interfaces")

    assert len(links) == 48
    assert links.directional_link_id.is_unique
    assert len(interfaces) == 12
    assert interfaces.physical_link_id.is_unique
    assert links.groupby("year").size().to_dict() == {"2040": 24, "2050": 24}
    assert links.groupby(["year", "link_family"]).size().to_dict() == {
        ("2040", "EXTERNAL_EXTERNAL"): 8,
        ("2040", "ITALY_FACING"): 16,
        ("2050", "EXTERNAL_EXTERNAL"): 8,
        ("2050", "ITALY_FACING"): 16,
    }
    assert links.p_nom_extendable.str.lower().eq("false").all()
    assert links.fixed_capacity.str.lower().eq("true").all()
    assert links.interconnector_asset_type.eq("NETWORK_LINK_NOT_GENERATOR").all()
    reverse = links.set_index("directional_link_id")
    for row in links.itertuples():
        counterpart = reverse.loc[row.reverse_directional_link_id]
        assert counterpart.from_market == row.to_market
        assert counterpart.to_market == row.from_market
        assert counterpart.year == row.year


def test_frozen_italy_interfaces_match_the_mem_contract() -> None:
    links = _read("links")
    italy = links.loc[links.link_family.eq("ITALY_FACING")]
    expected = {
        ("FR", "IT"): 4500, ("IT", "FR"): 2200,
        ("CH", "IT"): 4600, ("IT", "CH"): 1900,
        ("AT", "IT"): 700, ("IT", "AT"): 300,
        ("SI", "IT"): 1100, ("IT", "SI"): 1200,
        ("ME", "IT"): 600, ("IT", "ME"): 600,
        ("GR", "IT"): 500, ("IT", "GR"): 500,
        ("MT", "IT"): 200, ("IT", "MT"): 200,
        ("TN", "IT"): 600, ("IT", "TN"): 600,
    }
    expected_zones = {"FR": "NORD", "CH": "NORD", "AT": "NORD", "SI": "NORD", "ME": "CSUD", "GR": "SUD", "MT": "SICI", "TN": "SICI"}

    for year in ("2040", "2050"):
        horizon = italy.loc[italy.year.eq(year)]
        observed = {(row.from_market, row.to_market): int(row.capacity_MW) for row in horizon.itertuples()}
        assert observed == expected
        for row in horizon.itertuples():
            external = row.to_market if row.from_market == "IT" else row.from_market
            assert row.stage_b_receiving_zone == expected_zones[external]
            assert row.status == "FROZEN_MEM_ITALY_INTERFACE"


def test_external_links_use_accepted_2040_values_and_explicit_2050_carry_forward() -> None:
    links = _read("links")
    external = links.loc[links.link_family.eq("EXTERNAL_EXTERNAL")]
    expected = {
        ("AT", "CH"): 1200, ("CH", "AT"): 1200,
        ("AT", "SI"): 1450, ("SI", "AT"): 1450,
        ("CH", "FR"): 2200, ("FR", "CH"): 4500,
        ("HR", "SI"): 1500, ("SI", "HR"): 1500,
    }
    for year in ("2040", "2050"):
        horizon = external.loc[external.year.eq(year)]
        observed = {(row.from_market, row.to_market): int(row.capacity_MW) for row in horizon.itertuples()}
        assert observed == expected

    direct = external.loc[external.year.eq("2040")]
    carried = external.loc[external.year.eq("2050")]
    assert direct.status.eq("DIRECT_ACCEPTED_HORIZON_VALUE").all()
    assert direct.direct_vs_carry_forward.eq("DIRECT_2040_CONSTRUCTION").all()
    assert carried.status.eq("ACCEPTED_2040_CARRY_FORWARD_TO_2050").all()
    assert carried.direct_vs_carry_forward.eq("CARRY_FORWARD_FROM_2040").all()


def test_asymmetry_and_carry_forward_controls_are_explicit() -> None:
    interfaces = _read("interfaces")
    carry = _read("carry_forward")

    asymmetric = set(interfaces.loc[interfaces.asymmetric_2040.str.lower().eq("true"), "physical_link_id"])
    assert asymmetric == {"IT_FR", "IT_CH", "IT_AT", "IT_SI", "EXT_CH_FR"}
    assert len(carry) == 8
    assert carry.rule.eq("2040_NTC_CARRY_FORWARD_TO_2050").all()
    assert carry.interpolation_used.str.lower().eq("false").all()
    assert carry.extrapolation_used.str.lower().eq("false").all()
    assert pd.to_numeric(carry.source_capacity_MW).equals(pd.to_numeric(carry.target_capacity_MW))


def test_no_link_controls_are_the_exact_pair_complement_and_no_node_is_isolated() -> None:
    interfaces = _read("interfaces")
    no_links = _read("no_links")

    physical_pairs = {tuple(sorted((row.endpoint_a, row.endpoint_b))) for row in interfaces.itertuples()}
    excluded_pairs = {tuple(sorted((row.endpoint_a, row.endpoint_b))) for row in no_links.itertuples()}
    all_pairs = set(itertools.combinations(MARKETS, 2))
    assert len(no_links) == 33
    assert physical_pairs.isdisjoint(excluded_pairs)
    assert physical_pairs | excluded_pairs == all_pairs
    assert pd.to_numeric(no_links.capacity_MW).eq(0).all()
    assert set(interfaces.endpoint_a) | set(interfaces.endpoint_b) == set(MARKETS)


def test_krsko_is_physically_hosted_on_si_only_and_cors_is_absent() -> None:
    nodes = _read("nodes")
    links = _read("links")
    hosted = set(nodes.loc[nodes.krsko_physical_nuclear_host.str.lower().eq("true"), "market"])

    assert hosted == {"SI"}
    assert nodes.loc[nodes.market.eq("HR"), "croatia_krsko_entitlement_role"].eq("QA_ACCOUNTING_ONLY_NO_PHYSICAL_ASSET").all()
    assert "CORS" not in set(nodes.market)
    assert "CORS" not in set(links.from_market) | set(links.to_market)


def test_b5_rebuild_is_byte_deterministic(tmp_path: Path) -> None:
    rebuilt = tmp_path / "topology"
    build_topology_package(rebuilt)
    for filename in OUTPUT_FILES.values():
        assert (DEFAULT_OUTPUT_DIR / filename).read_bytes() == (rebuilt / filename).read_bytes()


def test_full_b5_validation_passes_without_network_solver_or_prices() -> None:
    result = validate_topology_package(
        DEFAULT_OUTPUT_DIR,
        write_reports=False,
        run_deterministic_rebuild=False,
    )
    assert result["qa"].status.eq("PASS").all()
    assert result["verification"]["scope"]["pypsa_network"] == "NOT_INSTANTIATED"
    assert result["verification"]["scope"]["gurobi_configuration"] == "NOT_PERFORMED"
    assert result["verification"]["scope"]["optimization"] == "NOT_RUN"
    assert result["verification"]["scope"]["market_prices"] == "NOT_PRODUCED"

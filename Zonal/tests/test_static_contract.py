from __future__ import annotations

import math

import pandas as pd

from mem_model.audit_sources import STATIC_EXPECTATIONS, frozen_static_audit
from mem_model.common import STATIC, ZONES


def test_frozen_files_have_exact_hashes_and_rows() -> None:
    manifest, checks = frozen_static_audit()
    assert len(manifest) == len(STATIC_EXPECTATIONS)
    assert set(manifest["status"]) == {"PASS"}
    assert set(checks["status"]) == {"PASS"}


def test_static_domains_and_nonextendability() -> None:
    generators = pd.read_csv(STATIC / "MEM_generators_static_final.csv")
    storage = pd.read_csv(STATIC / "MEM_storage_static_final.csv")
    assert set(generators["zone"]) == set(ZONES)
    assert not generators["p_nom_extendable"].astype(str).str.lower().isin(["true", "1"]).any()
    assert not storage["p_nom_extendable"].astype(str).str.lower().isin(["true", "1"]).any()
    assert not storage["e_nom_extendable"].astype(str).str.lower().isin(["true", "1"]).any()
    assert not generators["fuel"].astype(str).str.upper().isin(["COAL", "OIL", "PETROLEUM"]).any()


def test_phs_contract_controls() -> None:
    phs = pd.read_csv(STATIC / "MEM_PHS_Static_Runtime_Contract.csv")
    assert math.isclose(phs["operational_energy_MWh"].sum(), 53000.0, abs_tol=1e-6)
    assert math.isclose(phs["pump_power_MW"].sum(), 6400.0, abs_tol=1e-6)
    assert math.isclose(phs["discharge_power_MW_NET"].sum(), 7252.3, abs_tol=1e-6)
    assert not phs["operational_energy_MWh"].eq(626262.056948).any()


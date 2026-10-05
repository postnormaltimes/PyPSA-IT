"""Explicit MEM case contract for saved-network or result-table visualization.

Copy this adapter into MEM when ready, then map MEM's actual buses, carriers,
resources, and result-table names in configuration. No MEM filename or
generator-name convention is inferred here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json

import pandas as pd

from visualization_toolkit.io import ResultSource, load_network
from visualization_toolkit.network_maps import bundled_geography_path


@dataclass
class MEMCaseConfig:
    scenario_id: str
    year: int | str
    scenario_group: str
    output_directory: str | Path
    solved_network_path: str | Path | None = None
    market_mapping: dict[str, str] = field(default_factory=dict)
    carrier_mapping: dict[str, str] = field(default_factory=dict)
    selected_snapshots: dict[str, str] = field(default_factory=dict)
    result_tables: dict[str, str | Path] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    run_receipt: str | Path | None = None
    market_column: str | None = None
    shedding_generator_ids: list[str] = field(default_factory=list)
    vre_carriers: list[str] = field(default_factory=list)
    storage_carriers: list[str] = field(default_factory=list)
    virtual_generator_ids: list[str] = field(default_factory=list)
    residual_generator_ids: list[str] = field(default_factory=list)
    interconnector_ids: list[str] = field(default_factory=list)
    voll_price: float | None = None
    map: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MEMCaseConfig":
        return cls(**data)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "MEMCaseConfig":
        import yaml

        with Path(path).open(encoding="utf-8") as handle:
            data = yaml.safe_load(handle)
        if not isinstance(data, dict):
            raise ValueError("Case YAML must contain a mapping")
        return cls.from_dict(data)


@dataclass
class AdaptedCase:
    source: ResultSource
    scenario_id: str
    year: int | str
    scenario_group: str
    output_directory: Path
    market_mapping: dict[str, str]
    carrier_mapping: dict[str, str]
    selected_snapshots: dict[str, str]
    metadata: dict[str, Any]
    shedding_generator_ids: list[str]
    vre_carriers: list[str]
    storage_carriers: list[str]
    virtual_generator_ids: list[str]
    residual_generator_ids: list[str]
    interconnector_ids: list[str]
    voll_price: float | None
    map: dict[str, Any]

    def topology_map_kwargs(self) -> dict[str, Any]:
        """Explicit parameters for generic plot_zonal_physical_network."""
        if not self.market_mapping:
            raise ValueError("Map requires declared market_mapping or market_column")
        cfg = self.map
        return {
            "bus_grouping": self.market_mapping,
            "group_colors": cfg.get("group_colors"),
            "geography": cfg.get("geography") or bundled_geography_path(),
            "line_classification": cfg.get("line_classification"),
            "link_classification": cfg.get("link_classification"),
            "extent": cfg.get("extent"),
            "show_group_labels": cfg.get("show_group_labels", True),
            "show_bus_labels": cfg.get("show_bus_labels", False),
        }

    def scenario_record(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "year": self.year,
            "scenario_group": self.scenario_group,
            **self.metadata,
        }


def _read_table(path: Path) -> pd.DataFrame:
    if not path.is_file():
        raise FileNotFoundError(path)
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".json":
        return pd.read_json(path)
    raise ValueError(f"Unsupported result-table format: {path}")


def open_mem_case(config: MEMCaseConfig) -> AdaptedCase:
    """Open existing results and preserve the case's declared semantics."""
    if not config.scenario_id:
        raise ValueError("scenario_id is required")
    if not config.solved_network_path and not config.result_tables:
        raise ValueError("Provide solved_network_path and/or result_tables")
    source = (
        load_network(config.solved_network_path)
        if config.solved_network_path
        else ResultSource()
    )
    for table_name, path in config.result_tables.items():
        source.tables[table_name] = _read_table(Path(path))
    mapping = dict(config.market_mapping)
    if config.market_column:
        buses = source.component("buses")
        if config.market_column not in buses:
            raise KeyError(f"Declared market_column missing from buses: {config.market_column}")
        column_map = buses[config.market_column].dropna().astype(str).to_dict()
        mapping = {**column_map, **mapping}
    if mapping:
        buses = source.component("buses")
        unknown = set(mapping).difference(buses.index.astype(str))
        if unknown:
            raise ValueError(f"market_mapping names absent from buses: {sorted(unknown)[:8]}")
    metadata = dict(config.metadata)
    if config.run_receipt is not None:
        path = Path(config.run_receipt)
        if not path.is_file():
            raise FileNotFoundError(path)
        metadata["run_receipt_path"] = str(path)
        metadata["run_receipt"] = json.loads(path.read_text(encoding="utf-8"))
    source.metadata.update({
        "scenario_id": config.scenario_id,
        "year": config.year,
        "scenario_group": config.scenario_group,
    })
    return AdaptedCase(
        source=source,
        scenario_id=config.scenario_id,
        year=config.year,
        scenario_group=config.scenario_group,
        output_directory=Path(config.output_directory),
        market_mapping=mapping,
        carrier_mapping=dict(config.carrier_mapping),
        selected_snapshots=dict(config.selected_snapshots),
        metadata=metadata,
        shedding_generator_ids=list(config.shedding_generator_ids),
        vre_carriers=list(config.vre_carriers),
        storage_carriers=list(config.storage_carriers),
        virtual_generator_ids=list(config.virtual_generator_ids),
        residual_generator_ids=list(config.residual_generator_ids),
        interconnector_ids=list(config.interconnector_ids),
        voll_price=config.voll_price,
        map=dict(config.map),
    )

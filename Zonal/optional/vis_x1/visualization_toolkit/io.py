"""Read-only inputs for plotting saved PyPSA results or exported tables.

The toolkit deliberately has no optimisation, model-building, or write-back API.
Table names follow PyPSA components (buses, lines) and wide time series
(lines_t.p0). A long time-series table with snapshot/asset/value is accepted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd


def _time_index(frame: pd.DataFrame, name: str) -> pd.DataFrame:
    """Normalize a time-series table without assuming hourly snapshots."""
    result = frame.copy()
    if {"snapshot", "asset", "value"}.issubset(result.columns):
        result = result.pivot(index="snapshot", columns="asset", values="value")
    elif "snapshot" in result.columns:
        result = result.set_index("snapshot")
    elif result.index.name is None and len(result.columns):
        first = str(result.columns[0])
        if first.startswith("Unnamed:"):
            result = result.set_index(result.columns[0])
    if not isinstance(result.index, pd.MultiIndex):
        parsed = pd.to_datetime(result.index, errors="coerce")
        if parsed.notna().all():
            result.index = pd.DatetimeIndex(parsed, name="snapshot")
    if result.index.has_duplicates:
        raise ValueError(f"{name}: duplicate snapshots are ambiguous")
    return result.sort_index()


@dataclass
class ResultSource:
    """A saved network or explicitly named result tables."""

    network: Any | None = None
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def component(self, name: str) -> pd.DataFrame:
        if self.network is not None and hasattr(self.network, name):
            value = getattr(self.network, name)
            if isinstance(value, pd.DataFrame):
                return value
        if name in self.tables:
            value = self.tables[name]
            if "name" in value.columns and value["name"].is_unique:
                return value.set_index("name")
            if "asset" in value.columns and value["asset"].is_unique:
                return value.set_index("asset")
            return value
        raise KeyError(f"Missing component table '{name}'")

    def series(self, component: str, attr: str) -> pd.DataFrame:
        if self.network is not None:
            container = getattr(self.network, f"{component}_t", None)
            if container is not None:
                try:
                    value = getattr(container, attr)
                    if isinstance(value, pd.DataFrame) and not value.empty:
                        return value
                except (AttributeError, KeyError):
                    pass
        key = f"{component}_t.{attr}"
        if key not in self.tables:
            raise KeyError(f"Missing result time series '{key}'")
        return _time_index(self.tables[key], key)

    def table(self, name: str) -> pd.DataFrame:
        if name not in self.tables:
            raise KeyError(f"Missing result table '{name}'")
        return self.tables[name]

    @property
    def snapshots(self) -> pd.Index:
        if self.network is not None:
            return self.network.snapshots
        for name, table in self.tables.items():
            if "_t." in name or name == "snapshot_weightings":
                return _time_index(table, name).index
        raise KeyError("No snapshots: supply a network or time-series table")

    def weights(self, kind: str = "objective") -> pd.Series:
        """Return declared snapshot weights; unit weights only when absent.

        Consumers must disclose whether weights represent hours. For multiple
        investment periods, select one period before labeling duration as hours.
        """
        if self.network is not None:
            sw = getattr(self.network, "snapshot_weightings", None)
            if sw is not None and kind in sw:
                return pd.to_numeric(sw[kind], errors="raise").reindex(self.snapshots)
        if "snapshot_weightings" in self.tables:
            sw = _time_index(self.tables["snapshot_weightings"], "snapshot_weightings")
            if kind in sw:
                return pd.to_numeric(sw[kind], errors="raise").reindex(self.snapshots)
        return pd.Series(1.0, index=self.snapshots, name=f"{kind}_unit_weight")


def load_network(path: str | Path) -> ResultSource:
    """Load an existing NetCDF only. This never invokes a solver."""
    path = Path(path)
    if not path.is_file() or path.suffix.lower() != ".nc":
        raise ValueError(f"Expected an existing .nc network: {path}")
    import pypsa

    return ResultSource(network=pypsa.Network(str(path)), metadata={"source_path": str(path)})


def load_tables(directory: str | Path, metadata: Mapping[str, Any] | None = None) -> ResultSource:
    """Load a directory of named CSV, Parquet, or JSON result tables."""
    directory = Path(directory)
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    tables: dict[str, pd.DataFrame] = {}
    for path in sorted(directory.iterdir()):
        if path.suffix.lower() == ".csv":
            tables[path.stem] = pd.read_csv(path)
        elif path.suffix.lower() == ".parquet":
            tables[path.stem] = pd.read_parquet(path)
        elif path.suffix.lower() == ".json":
            tables[path.stem] = pd.read_json(path)
    if not tables:
        raise ValueError(f"No CSV, Parquet, or JSON tables found in {directory}")
    return ResultSource(tables=tables, metadata={"source_path": str(directory), **dict(metadata or {})})


def as_source(value: ResultSource | str | Path | Any) -> ResultSource:
    if isinstance(value, ResultSource):
        return value
    if isinstance(value, (str, Path)):
        path = Path(value)
        return load_tables(path) if path.is_dir() else load_network(path)
    if hasattr(value, "buses") and hasattr(value, "snapshots"):
        return ResultSource(network=value)
    if isinstance(value, Mapping):
        tables = {str(k): v for k, v in value.items() if isinstance(v, pd.DataFrame)}
        if tables:
            return ResultSource(tables=tables)
    raise TypeError("Expected ResultSource, PyPSA Network, .nc path, table directory, or table mapping")


def save_figure(fig: Any, path: str | Path, *, dpi: int = 240) -> Path:
    """Save a standalone PNG, SVG, or PDF with room for labels."""
    path = Path(path)
    if path.suffix.lower() not in {".png", ".svg", ".pdf"}:
        raise ValueError("Figure output must be .png, .svg, or .pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=dpi, bbox_inches="tight", facecolor="white")
    return path

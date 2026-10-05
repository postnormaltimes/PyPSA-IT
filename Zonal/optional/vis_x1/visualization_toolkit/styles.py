"""Presentation styles, independent of model and market semantics."""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
from hashlib import sha256
from typing import Any, Iterator, Mapping

import matplotlib as mpl
from matplotlib import font_manager


@dataclass
class PlotStyle:
    carrier_style: dict[str, dict[str, Any]] = field(default_factory=dict)
    line_style: dict[str, Any] = field(default_factory=dict)
    map_style: dict[str, Any] = field(default_factory=dict)
    scenario_style: dict[str, dict[str, Any]] = field(default_factory=dict)
    carrier_order: list[str] = field(default_factory=list)
    font_family: str | None = None

    def color(self, carrier: str, network: Any | None = None) -> str:
        entry = self.carrier_style.get(str(carrier), {})
        if "color" in entry:
            return str(entry["color"])
        if network is not None and hasattr(network, "carriers"):
            carriers = network.carriers
            if carrier in carriers.index and "color" in carriers:
                value = carriers.at[carrier, "color"]
                if isinstance(value, str) and value.strip():
                    return value
        palette = mpl.colormaps["tab20"].colors
        index = int.from_bytes(sha256(str(carrier).encode()).digest()[:4], "big") % len(palette)
        return mpl.colors.to_hex(palette[index])

    def label(self, carrier: str) -> str:
        return str(self.carrier_style.get(str(carrier), {}).get("label", carrier))

    def ordered(self, carriers: list[str]) -> list[str]:
        existing = set(carriers)
        listed = [c for c in self.carrier_order if c in existing]
        return listed + sorted(existing.difference(listed), key=str.casefold)

    def scenario_color(self, scenario_id: str) -> str:
        entry = self.scenario_style.get(str(scenario_id), {})
        return str(entry.get("color") or self.color(f"scenario:{scenario_id}"))


def style_from_config(config: Mapping[str, Any] | None = None) -> PlotStyle:
    data = dict(config or {})
    return PlotStyle(
        carrier_style=dict(data.get("carrier_style") or {}),
        line_style=dict(data.get("line_style") or {}),
        map_style=dict(data.get("map_style") or {}),
        scenario_style=dict(data.get("scenario_style") or {}),
        carrier_order=list(data.get("carrier_order") or []),
        font_family=data.get("font_family"),
    )


@contextmanager
def style_context(style: PlotStyle | None = None) -> Iterator[None]:
    style = style or PlotStyle()
    available = {f.name for f in font_manager.fontManager.ttflist}
    requested = style.font_family or "Aptos"
    font = requested if requested in available else "DejaVu Sans"
    with mpl.rc_context({
        "font.family": font,
        "figure.facecolor": "white",
        "axes.facecolor": "white",
        "savefig.facecolor": "white",
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.grid": False,
        "figure.dpi": 120,
        "savefig.dpi": 240,
        "axes.titlesize": 12,
        "axes.labelsize": 10,
        "legend.fontsize": 9,
    }):
        yield

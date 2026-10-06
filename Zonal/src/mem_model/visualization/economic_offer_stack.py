"""One opt-in PNG: observed equilibrium-value blocks, no invented offer MW."""
from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Patch, Rectangle
from matplotlib.lines import Line2D
import numpy as np
import yaml

from ..reporting.canonical_results import reporting_config
from ..reporting.economic_offer_stack import require_complete_stack, cumulative_blocks
from .vis_x1 import CONFIG, _toolkit


def render_review(stack, output, *, fixture_label, config):
    """Complete fixed-LP population only; roots are annotations, never widths."""
    records = require_complete_stack(stack)
    blocks = cumulative_blocks(stack)
    output = Path(output)
    if output.suffix.lower() != ".png" or output.exists():
        raise ValueError("Single new PNG review target required")
    _toolkit(yaml.safe_load(CONFIG.read_text(encoding="utf-8")))
    from visualization_toolkit.styles import PlotStyle, style_context
    rc = reporting_config()
    style = PlotStyle(font_family=rc["font_preference"][0])
    labels = rc["carrier_to_display_technology"]
    family_labels = {"BESS": "BESS", "PHS": "PHS", "Reservoir / pondage hydro": "Hydro - reservoir"}
    colors = rc["technology_colors"]
    def visual(row):
        label = family_labels.get(row.economic_group, labels.get(row.carrier))
        if label not in colors:
            raise ValueError(f"Explicit technology style missing: {row.technology}/{row.carrier}")
        return ("Reservoir / pondage hydro" if row.economic_group == "Reservoir / pondage hydro" else label), colors[label]
    first = records.iloc[0]
    price = float(first.zonal_price_EUR_MWh)
    width = float(blocks.end_MW.max())
    direct = blocks.direct_cost_contribution_EUR_MWh.to_numpy(float)
    effective = blocks.effective_marginal_value_EUR_MWh.to_numpy(float)
    if not np.isfinite(price):
        raise ValueError("Missing fixed-LP zonal price")
    values = np.r_[0., price, effective, direct[np.isfinite(direct)]]
    span = max(float(np.ptp(values)), 10.)
    low, high = min(0., values.min()) - .12*span, values.max() + .23*span
    with style_context(style):
        fig, ax = plt.subplots(figsize=(config["render"]["width_inches"], config["render"]["height_inches"]))
        fig.subplots_adjust(left=.09, right=.97, bottom=.30, top=.73)
        legend = {}
        for r in blocks.itertuples(index=False):
            label, color = visual(r)
            value = r.effective_marginal_value_EUR_MWh
            ax.add_patch(Rectangle((r.start_MW, min(0., value)), r.cleared_MW, abs(value), color=color, linewidth=0, alpha=.68))
            ax.plot([r.start_MW, r.end_MW], [value, value], color=color, linewidth=2.5)
            if np.isfinite(r.direct_cost_contribution_EUR_MWh):
                ax.plot([r.start_MW, r.end_MW], [r.direct_cost_contribution_EUR_MWh]*2, color="#555555", linewidth=1.2, linestyle="--", zorder=5)
            legend[label] = Patch(facecolor=color, edgecolor="none", label=label)
        ax.axhline(price, color="#333333", lw=1.1, ls=":", zorder=6)
        ax.axvline(width, color="#333333", lw=.8, ls="--")
        withdrawal = float(records.loc[records.side.eq("DEMAND"), "dispatched_MW"].sum())
        ax.axvline(withdrawal, color="#555555", lw=.8, ls="-.")
        ax.annotate(f"Fixed-LP zonal price: {price:.2f} EUR/MWh", (width*.01, price), xytext=(0, 9), textcoords="offset points", fontsize=10)
        ax.annotate(f"Cleared supply: {width:,.0f} MW\nFixed-LP withdrawals: {withdrawal:,.0f} MW", (max(width, withdrawal), high-.07*span), xytext=(-8, 0), textcoords="offset points", ha="right", va="top", fontsize=9)
        ax.set_xlim(0, max(width, withdrawal)*1.05)
        ax.set_ylim(low, high)
        ax.set_xlabel("Cumulative cleared electrical supply [MW]")
        ax.set_ylabel("Equilibrium marginal value [EUR/MWh]")
        ax.grid(axis="y", color="#E6E8EB", linewidth=.5)
        ax.set_axisbelow(True)
        ax.axhline(0, color="#777777", lw=.6)
        handles = list(legend.values()) + [Line2D([], [], color="#555555", ls="--", lw=1.2, label="Direct cost contribution (electrical basis)"), Line2D([], [], color="#333333", ls=":", label="Fixed-LP zonal price")]
        ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0, -.20), ncol=2, frameon=False, fontsize=9)
        fig.text(.09, .96, "Model-implied economic equilibrium stack — review prototype", fontsize=16, weight="bold", va="top")
        fig.text(.09, .90, f"{fixture_label} | {first.zone} | {first.snapshot:%d %b %Y, %H:%M} | Regime: {first.marginal_regime}", fontsize=11)
        fig.text(.09, .85, "REVIEW / REFERENCE ONLY · NOT CANONICAL · NOT ROUTINE · SUBJECT TO FINAL V2 METHODOLOGY", fontsize=9, color="#8C564B")
        fig.text(.09, .79, "Complete fixed-LP resource population; validated roots annotate it. Unused capacity is not valued or plotted.", fontsize=9, color="#697386")
        opp = records.loc[records.side.eq("SUPPLY"), "opportunity_value_EUR_MWh"].dropna()
        note = f"{opp.min():.2f}–{opp.max():.2f} EUR/MWh" if len(opp) else "unresolved in supplied decomposition"
        fig.text(.09, .10, f"Opportunity-value contribution: {note}.\nAll quantities, prices and annotations share the same fixed-commitment LP lineage.", fontsize=9, color="#697386")
        fig.text(.09, .035, "Ex-post solved-point representation, not submitted participant bids or a counterfactual supply curve.", fontsize=9, color="#697386")
        font = plt.rcParams["font.family"][0]
        try:
            fig.savefig(output, dpi=config["render"]["dpi"])
        finally:
            plt.close(fig)
    return output, font

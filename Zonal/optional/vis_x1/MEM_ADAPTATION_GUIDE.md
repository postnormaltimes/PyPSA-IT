# MEM adapter and plotting contract

## 1. Case declaration

Create one configuration per solved MEM case from
[examples/example_config.yaml](examples/example_config.yaml). Required:
solved_network_path or result_tables; scenario_id; year; scenario_group;
output_directory. The names of cases may be 2040 Slow/Base/High and 2050
Slow/Base/High or any other values. The toolkit never parses year or policy
from a scenario_id string.

Declare bus-to-market mapping with market_mapping. As a deliberate alternative,
market_column may name a saved buses column that already holds authoritative
market labels; explicit market_mapping entries override that column. This
enables any set of European or neighboring markets without a central country.
Do not infer market from bus or generator names.

Declare carrier_mapping, VRE and storage carrier sets, shedding_generator_ids,
virtual_generator_ids, residual_generator_ids and interconnector_ids from MEM
configuration or run manifests. Keep virtual and residual resources labeled in
the figure data so that their meaning is visible. Optional result_tables maps a
logical PyPSA table name to CSV/Parquet/JSON; run_receipt points to an existing
JSON receipt. selected_snapshots is a reproducible label-to-snapshot map.

## NETWORK TOPOLOGY AND MARKET-GEOGRAPHY MAPS

`plot_zonal_physical_network(case.source, **case.topology_map_kwargs())`
accepts the physical buses and branches from MEM's saved network or equivalent
tables. Provide every market bus through `market_mapping`, or set
`market_column` to an authoritative Buses field. An unmapped/external bus is
drawn neutral gray; it never inherits an arbitrary market color. The map's
groups can represent MEM markets, countries, or declared model regions.
Grouping changes only bus display, never topology or branch direction.

The `map` case configuration is passed through the MEM adapter:

```yaml
market_column: market
market_mapping: {}                 # optional explicit overrides by bus ID
map:
  geography: null                 # bundled Natural Earth; or CRS-declared path
  extent: [-11, 27, 30, 53]       # xmin, xmax, ymin, ymax in lon/lat
  group_colors: {Italy: "#4c78a8", France: "#e45756"}
  line_classification: {}         # exact branch ID: internal/external
  link_classification: {}         # exact Link ID: internal/external
  show_group_labels: true
  show_bus_labels: false
```

When `map.geography` is null or omitted, `bundled_geography_path()` resolves
the packaged Natural Earth layer relative to the installed toolkit. No PyPSA-IT
repository path is required. The supplied geography may be a GeoJSON/Shapefile path or a CRS-declared
GeoDataFrame; the generic map converts it to EPSG:4326. The package includes
`visualization_toolkit/data/naturalearth_lowres.geojson` for a plotting-only fallback covering
Europe and neighboring regions. MEM may use a more detailed geography. Country
borders and coastlines are visual context, not market or branch constraints.
The generic renderer plots AC Lines/Transformers solid, controllable Links
dashed, and explicitly classified external/boundary branches separately.
It does not infer HVDC or market semantics from names. The group and branch
palettes are configurable in `map.group_colors` and `PlotStyle.line_style`.

Pass the **same** `geography` and `extent` to `plot_network_map`,
`plot_capacity_map`, `plot_congestion_map`, `plot_flow_map`, and
`plot_price_map`. The maps then share bus coordinates, branch segments and
geographic scaffold. `plot_flow_map(source, snapshot, ...)` uses a selected
saved result hour and signed bus0 MW; `plot_congestion_map` uses horizon-wide
weighted loading hours. `compute_system_stress_table` provides a reproducible
`stress_rank` and the underlying snapshot metrics. Select the rank-1 hour and
persist the table, rather than choosing a single branch's maximum. Capacity
and price fields are selected by their respective plot arguments; map them
to MEM's actual saved result fields in its adapter. Save figures with
`save_figure` to PNG, SVG or PDF.

## 2. Tabular inputs

The generic input layer accepts a saved PyPSA .nc or named tables. Minimal
examples:

| Plot | Required tables (or equivalent Network fields) |
| --- | --- |
| Base map | buses with x/y; lines/links/transformers with bus0/bus1 |
| Capacity map | buses; generators/storage_units/stores; declared rating units |
| Flow map | buses; branches; lines_t.p0 / links_t.p0 / transformers_t.p0 |
| Congestion | branch ratings and p0; q0 improves AC loading to MVA |
| Dispatch | generators with carrier; generators_t.p; loads_t.p or p_set |
| Price | buses_t.marginal_price; market_mapping for market view |
| Storage | storage_units_t.p or p_dispatch/p_store; state_of_charge; stores_t.p/e if used |
| Scenario | tidy scenario metric table plus scenario metadata |

Time-series CSV may be wide (snapshot, asset A, asset B) or long
(snapshot, asset, value). The index must identify exact snapshots. Missing
series raise errors; plots do not synthesize output from model inputs.
snapshot_weightings.csv may declare objective, generators and stores columns.
Unit weights are used when absent and must be disclosed in a report.

For result tables produced by MEM processing, normalize them to these logical
names in the adapter. Do not make the generic core understand Stage-A/Stage-B
or MEM-specific QA manifests.

## 3. Signed quantities and weights

For branch bus0 to bus1, positive p0 leaves bus0; negative p0 reverses
direction. Intermarket exports/imports follow the **declared** market of each
terminal. A multi-terminal Link needs a case-specific contract for extra ports
and should not be silently collapsed to two terminals.

Use snapshot_weightings.generators for energy-style MW-to-MWh aggregation,
unless the MEM result contract declares a different physical-hour weight.
Duration curves should show cumulative weighted hours only when weights mean
hours. For representative or multi-investment-period snapshots, partition and
label periods explicitly. Price statistics need the selected aggregation
(nodal, simple time, load-weighted, market-specific) in the caption.

AC branch active-power utilization uses abs(p0)/rating. If q0 is present,
apparent utilization uses sqrt(p0^2+q0^2)/rating. Link utilization uses
abs(p0)/p_nom at bus0; this is not the receiving-end transfer under losses.
A usage threshold is a screening indicator; a shadow-price or constraint
dual must be supplied to claim binding.

## 4. Cost and QA interpretation

Treat objective, capex, opex, fixed costs and penalty terms as distinct fields.
The toolkit does not import PyPSA-IT C_design accounting into MEM. A total cost
comparison is valid only after MEM confirms a common accounting scope and
currency-year basis. Show solver termination and run receipt alongside plots
where available.

Shedding and scarcity are explicit resource mappings. A high marginal price
alone does not prove involuntary load shedding. Curtailment equals available
potential minus dispatched generation for declared resources, clipped only
after checking sign and technical limits. Balance residuals or headroom must
come from a declared QA table or independently defined extraction.

## 5. First MEM integration steps

1. Select one already solved MEM 2040 or 2050 network and its existing result
   receipt; do not run a new solve.
2. Fill one config with the actual bus markets, carrier groups, explicit
   virtual/residual/shedding asset IDs, weight meanings and output directory.
3. Render base map, branch loading/congestion, one selected-hour flow map,
   dispatch+demand, market prices and storage panels.
4. Compare each figure's underlying table to MEM's existing result exports.
   Resolve unit/sign/weight differences in the MEM adapter.
5. Add all scenario configs to a manifest and build tidy comparison metrics.
   Preserve distinct objective/cost scopes and scenario metadata.
6. Surface MEM run receipt and QA flags with the figures. A plot does not
   certify the solution.

Remaining MEM-specific work: actual mapping values, Stage-A/Stage-B artifact
selection, multi-terminal link contracts if present, virtual and residual
generator semantics, cost scope and emissions factors, and authoritative QA
receipt mapping. Those cannot be guessed from the PyPSA-IT files.

## Concrete MEM-side figure contract

`MEMCaseConfig` requires `scenario_id`, `year`, `scenario_group`, and
`output_directory`, plus `solved_network_path` or declared `result_tables`.
Supply `market_mapping` or `market_column` (the bus grouping field), and
`carrier_mapping` from MEM's accepted configuration. `map.group_colors` is
optional; its keys are the declared market labels. `map.line_classification`
and `map.link_classification` map exact branch IDs to the intended display
class; the renderer keeps AC Lines/Transformers and controllable Links distinct.
`map.geography` may be null for bundled country outlines or a CRS-declared
European file; `map.extent` bounds the display. `selected_snapshots`,
`result_tables`, `run_receipt`, and `metadata` are optional but must be
included when needed to preserve MEM's accepted result and QA semantics.

1. **Market-coloured physical topology:** call
   `plot_zonal_physical_network(case.source, **case.topology_map_kwargs())`.
   Verify the returned bus/branch QA table against the saved network.
2. **Congestion:** call `plot_congestion_map` with the same geography/extent;
   preserve its weighted threshold-hour table and active/apparent-power caveat.
3. **Selected-hour MW flow:** call `compute_system_stress_table`, persist it,
   then call `plot_flow_map` at the rank-1 snapshot or an explicitly declared
   `selected_snapshots` entry. Keep Link bus0 sign and receiving-terminal
   losses distinct.
4. **Stacked dispatch:** call `plot_stacked_dispatch` with declared
   `carrier_mapping`; label virtual, residual and shedding resources from
   explicit ID lists rather than names.
5. **Storage/SOC:** call `plot_storage_operation` for each declared
   `storage_carriers` entry, respecting StorageUnit versus Store energy units.
6. **Prices:** use `market_price_table`, `plot_price_timeseries`,
   `plot_price_duration`, and `plot_price_heatmap` with the market mapping
   and an explicitly described aggregation/weighting basis.
7. **Inter-market exchange:** use `extract_intermarket_flows`,
   `market_exchange_timeseries`, and `plot_market_exchange`; use terminal-specific
   Link flows and declare any multi-port Link treatment.
8. **Generation/capacity:** use `extract_generation_mix`, `extract_capacity_mix`,
   and their plotting functions; preserve zero optimized capacity and keep
   MW, MWh and Store energy separate.
9. **Scarcity/shedding:** pass `shedding_generator_ids` and `voll_price` only
   when supplied by MEM governance/receipts; use the toolkit's summary tables
   and label unverified fields as unavailable.
10. **2040/2050 comparison:** assemble tidy scenario metrics with
    `prepare_scenario_metrics` and render `plot_scenario_comparison`; order by
    `scenario_id`/`year`/`scenario_group` metadata, with no six-case hardcode.
    Compare objective, cost, emissions and QA only when their MEM definitions
    are comparable and present.

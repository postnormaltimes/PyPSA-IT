# MEM v2.9 frozen data contract

## Contract purpose

The independent MEM runtime consumes versioned frozen files only. Source acquisition and derivation occur before the solve; the solve must not call Terna, GEM, PyPSA-IT, powerplantmatching, weather services, or any mutable external API.

Every release has a manifest containing file name, byte size, SHA-256, schema version, row count, source edition, retrieval date, derivation code commit, scenario, horizon, weather/calendar year, timezone, units, evidence class, reviewer and approval status. Raw source files remain immutable under `data/raw/`; transformations write to `data/derived/`; approved solver inputs are copied to `data/frozen/<release_id>/`.

## Global conventions

- `schema_version`: `mem-v2.9` until explicitly superseded.
- Italian zone codes: exactly `NORD`, `CNOR`, `CSUD`, `SUD`, `CALA`, `SICI`, `SARD`.
- Scenario IDs: `IT2040_SLOW`, `IT2040_BASE`, `IT2040_HIGH`, `IT2050_SLOW`, `IT2050_BASE`, `IT2050_HIGH`.
- Timestamps: ISO-8601 UTC, hourly start-of-interval, monotonic and unique.
- Calendar: a complete UTC year, 8,760 or 8,784 rows per time-series key.
- Power and energy: MW and MWh unless the column name explicitly states otherwise.
- Prices and costs: real EUR in the declared price year; electrical-output marginal costs in EUR/MWh_e.
- Capacity basis: `NET`, `GROSS`, or `MODEL_NOMINAL` must be explicit. No gross/net conversion is implicit.
- Missing values: empty values are rejected for mandatory fields. Zero is a value and must not be used as a missing-data marker.
- IDs are stable, unique strings and are never regenerated from row numbers.
- Every row carries or joins to `source_id`, `evidence_class`, `status`, and `notes` in the provenance layer.

## 1. `zones.csv`

**Primary key:** `zone`

| Column | Type | Required | Rule |
|---|---|---:|---|
| `zone` | string | yes | One of the seven frozen Italian codes |
| `country` | string | yes | `IT` |
| `display_name` | string | yes | Human-readable market-zone name |
| `active_from` | date | yes | Official topology validity start |
| `active_to` | date/null | no | Null if valid through study horizon |
| `source_id` | string | yes | Official topology evidence |
| `evidence_class` | enum | yes | `PYPSA IMPLEMENTATION MAPPING` or direct topology evidence |
| `status` | enum | yes | `VERIFIED` required for a frozen release |

**Validation:** exactly seven rows and no other Italian bus code. External buses do not appear here.

## 2. `generators.csv`

**Primary key:** `generator_id`; **grain:** one physical thermal unit/plant band or one `zone x variable-renewable technology` aggregate.

Mandatory fields:

`generator_id`, `zone`, `carrier`, `technology`, `fuel`, `p_nom_MW`, `capacity_basis`, `efficiency_pu`, `vom_EUR_per_MWh_e`, `co2_t_per_MWh_th`, `commissioning_year`, `status`, `is_chp`, `aggregation_level`, `marginal_cost_method`, `source_id`, `evidence_class`, `reconciliation_status`, `notes`.

Optional but preferred fields include plant name, unit name, GEM unit ID, owner, latitude, longitude, location accuracy, retirement year, minimum stable output and ramp metadata. Minimum stable output and ramps remain metadata unless the model scope is later expanded; they do not activate unit commitment.

Rules:

- `p_nom_MW > 0`; no extendable capacity.
- Current Italian thermoelectric MW must reconcile to the Terna 2024 NET zone/technology control. A row whose sole capacity source is powerplantmatching is rejected.
- GEM gross MW is stored in the reconciliation layer and is not silently written into `p_nom_MW`.
- `technology` and `fuel` are separate. CHP is a separate Boolean/role field.
- Variable renewable rows use `aggregation_level=ZONE_TECHNOLOGY`; thermal rows use `UNIT`, `PLANT`, or `ZONE_TECH_COST_BAND` with a reason.
- Long-duration retired or mothballed capacity is excluded from available `p_nom` unless the scenario explicitly retains it and records the evidence.
- Static and hourly marginal-cost methods are mutually exclusive per run.

## 3. `generator_availability_hourly.parquet`

**Primary key:** (`timestamp`, `generator_id`)

| Column | Type | Rule |
|---|---|---|
| `timestamp` | UTC datetime | Complete annual hourly index |
| `generator_id` | string | Foreign key to `generators.csv` |
| `p_max_pu` | float | Finite, `0 <= value <= 1` |
| `p_min_pu` | float | Optional; finite and `0 <= p_min_pu <= p_max_pu` |
| `availability_type` | enum | Weather, planned maintenance, forced/unplanned, environmental, or combined |
| `source_id` | string | Provenance |

Every generator must have either a complete profile or an explicitly approved static default. Scheduled maintenance, forced outage treatment and long-duration capacity removal must not be double counted.

## 4. `load_hourly.parquet`

**Primary key:** (`timestamp`, `zone`)

Columns: `timestamp`, `zone`, `load_MW`, `load_definition`, `source_id`, `evidence_class`, `quality_flag`.

Rules:

- Seven finite non-negative values per hour.
- Annual zonal sums reconcile to the workbook scenario allocations and national rigid-load target within a declared tolerance.
- Power-to-X or other flexible demand is represented separately if it has decision bounds; it is not silently embedded in rigid load.
- The network attaches rigid demand through PyPSA `Load.p_set`.

## 5. `storage.csv`

**Primary key:** `storage_id`

Mandatory fields:

`storage_id`, `zone`, `technology`, `component_form`, `p_charge_nom_MW`, `p_discharge_nom_MW`, `e_nom_MWh`, `efficiency_charge_pu`, `efficiency_discharge_pu`, `standing_loss_per_hour`, `initial_state_rule`, `terminal_state_rule`, `cyclic_state`, `inflow_required`, `spill_allowed`, `source_id`, `evidence_class`, `status`, `notes`.

Rules:

- Power and energy are fixed and non-negative; efficiencies are in `(0,1]`; standing loss is in `[0,1)`.
- Use `StorageUnit` only if its symmetric-power structure represents the asset faithfully. Otherwise use `Store` plus fixed charge/discharge Links.
- Pumped hydro, reservoir hydro and batteries are separate technologies with explicit inflow and state rules.
- Initial state, terminal state and cyclicity cannot all be left unspecified.

## 6. `hydro_inflow_hourly.parquet`

**Primary key:** (`timestamp`, `storage_id`)

Columns: `timestamp`, `storage_id`, `inflow_MW`, `source_weather_year`, `source_id`, `quality_flag`.

Rules: complete annual UTC coverage for every storage asset with `inflow_required=true`; finite non-negative values; no silent forward fill; annual inflow energy reconciled to the selected historical or scenario control.

## 7. `interzonal_capacities.csv`

**Primary key:** `link_id`; **grain:** one direction for one study period.

Mandatory fields:

`link_id`, `bus0`, `bus1`, `direction_label`, `capacity_MW`, `capacity_time_basis`, `capacity_side`, `efficiency_pu`, `marginal_toll_EUR_per_MWh`, `active_from`, `active_to`, `source_id`, `evidence_class`, `status`, `notes`.

Rules:

- `bus0` and `bus1` are distinct Italian zones.
- One non-negative fixed `Link` per direction; a reverse direction has a separate `link_id`.
- `capacity_time_basis` is `ANNUAL_STATIC` or points to a separately frozen hourly table. No historical realized flow is used as `p_set`.
- Sending-side/receiving-side convention and any losses are explicit.
- No Italian PyPSA `Line` is created.

## 8. `external_interfaces.csv`

**Primary key:** `interface_id`; **grain:** one Italian/external bidding-zone pair and direction set.

Mandatory fields:

`interface_id`, `external_bidding_zone`, `italian_zone`, `import_capacity_MW`, `export_capacity_MW`, `capacity_time_basis`, `capacity_side`, `import_efficiency_pu`, `export_efficiency_pu`, `import_toll_EUR_per_MWh`, `export_toll_EUR_per_MWh`, `active_from`, `active_to`, `mapping_status`, `source_id`, `evidence_class`, `notes`.

Rules:

- Capacities are fixed and non-extendable.
- `mapping_status=VERIFIED` is required for a frozen solve.
- FR/CH/AT/SI to NORD, GR to SUD and ME to CSUD are provisional until study-year verification. Tunisia and Malta remain candidate evidence only.
- Auxiliary virtual-generator/load bounds are derived from a documented safe system-energy bound and must be demonstrated non-binding.

## 9. `external_prices_hourly.parquet`

**Primary key:** (`timestamp`, `external_bidding_zone`)

Columns: `timestamp`, `external_bidding_zone`, `price_EUR_per_MWh`, `price_basis`, `source_year`, `scenario_method`, `source_id`, `quality_flag`.

Rules: complete annual UTC coverage for every external zone; no missing or infinite values; currency and real-price year declared; future-price construction reproducible; negative prices retained; no historical power-flow schedule embedded.

## 10. `cost_assumptions.csv`

**Primary key:** `assumption_id`

Mandatory fields:

`assumption_id`, `scenario_id`, `horizon_year`, `carrier`, `fuel_price_EUR_per_MWh_th`, `co2_price_EUR_per_t`, `co2_t_per_MWh_th`, `vom_EUR_per_MWh_e`, `efficiency_basis`, `marginal_cost_method`, `price_year`, `source_id`, `evidence_class`, `status`, `notes`.

Rules:

- The static formula is `fuel_price / efficiency + CO2_price x CO2_intensity / efficiency + VOM`, unless a documented alternative is selected.
- Hourly fuel or CO2 series, if later introduced, replace rather than add to static components.
- Technology parameters and fuel price assumptions retain separate provenance.
- Interim assumptions remain labelled and sensitivity-tested.

## Provenance and reconciliation sidecars

The frozen release also includes:

- `source_manifest.csv`: `source_id`, title, publisher, edition, URL/path, retrieval date, license, SHA-256 and citation.
- `derivation_manifest.csv`: output file, transformation, code commit, parameters, input hashes and reviewer.
- `thermal_reconciliation.csv`: Terna control rows joined to GEM units, gross/net qualifications, current-status bridge and unresolved residuals.
- `release_manifest.csv`: all frozen file hashes, sizes, row counts and validation results.

These sidecars are required even though the ten runtime tables above are the only solver-facing data files.

## Cross-file validation

The freeze command must fail if:

1. a foreign key is missing or duplicated;
2. the Italian bus set is not exactly the seven frozen zones;
3. any time-series key is incomplete, duplicated, non-monotonic or timezone-naive;
4. a capacity, efficiency, availability, load or price violates its domain;
5. any nominal capacity is marked extendable;
6. a current thermal row lacks Terna control provenance or has an unresolved gross/net substitution;
7. annual zonal load, FER, storage or thermal totals do not reconcile to their declared controls;
8. a 55 GW, 30 GW, 15.5136 GW, 18.9449 GW or 48.9449 GW benchmark is inserted as a capacity without the evidence role specified in the methodology;
9. storage state rules are incomplete;
10. external interfaces or costs cannot be reconciled to a net settlement convention.

## External-market cost identity

For reporting, calculate:

`net_external_trade_cost = import_energy x external_price - export_energy x external_price + interface_tolls + monetized_losses`

by external zone and hour, using the applicable sending/receiving-side convention. Reconcile this value to the optimization objective after subtracting the constant objective contribution of the fixed external virtual load. Domestic production cost and external trade settlement are reported separately.

## Release readiness

A contract can be structurally valid while its inputs remain `REQUIRES DATA`. The current v2.9 release freezes schemas and rejection rules only. The Terna/GEM thermal stack, load, availability, storage/hydro, commercial transfer and future external-price datasets are not yet empirical frozen releases.

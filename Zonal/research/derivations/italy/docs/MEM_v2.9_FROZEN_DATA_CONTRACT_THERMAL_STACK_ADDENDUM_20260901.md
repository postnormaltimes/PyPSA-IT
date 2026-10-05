# MEM v2.9 frozen data contract — thermal-stack empirical addendum

## Purpose and precedence

This addendum refines only the thermal-stack acquisition and derivation contracts. It does not redesign the accepted v2.9 architecture. The independent MEM runtime continues to consume frozen files and must not call Terna, GEM, PyPSA-IT or powerplantmatching at runtime.

## Frozen empirical files now available

### `Terna_Thermoelectric_Capacity_2024_Canonical.csv`

**Primary key:** `record_id`.

**Canonical grain:** one unique `year × capacity_basis × region × province × category × subcategory` record after explicit aggregation of multiple official source rows at that visible grain.

Required fields now frozen:

`record_id`, `year`, `capacity_type_original`, `capacity_basis`, `region_code`, `region_original`, `region_normalized`, `province_code`, `province_original`, `province_normalized`, `market_zone`, `category_original`, `category_normalized`, `chp_flag`, `subcategory_original`, `technology_normalized`, `efficient_power_MW_raw`, `efficient_power_MW`, `raw_cell_type`, `parser_status`, `source_row_count`, `source_row_numbers`, `source_grain_status`, `acquisition_date`, `acquisition_route`, `source_id`, `raw_source_file`, `archived_raw_file`, `raw_source_sha256`.

Validation:

- `year=2024` and `capacity_basis=NET` for every canonical row;
- non-negative finite MW;
- unique canonical grain;
- every province maps exactly once;
- market zone is one of the seven canonical codes;
- national total reconciles to 60,331.82927 MW;
- CCGT-like and gas-turbine-like controls reconcile to 41,739.11900 MW and 3,619.37798 MW;
- technology does not imply fuel;
- repeated official source rows remain traceable.

### `Terna_Thermoelectric_Production_2024_Canonical.csv`

**Primary key:** `record_id`.

**Canonical grain:** one `year × region × province × category × subcategory` production pair.

Required production fields include:

`production_netta_GWh_raw`, `production_netta_GWh`, `production_lorda_GWh_raw`, `production_lorda_GWh`, `pair_values_equal`, `production_basis_original`, `netta_source_row_number`, `lorda_source_row_number`, `paired_source_row_numbers`, `basis_resolution_method`, `basis_resolution_status`.

Validation:

- exactly two source rows per visible grain;
- higher value is Lorda and lower value is Netta;
- `production_lorda_GWh >= production_netta_GWh`;
- equal pairs use `AMBIGUOUS_EQUAL_VALUES_NO_ROW_ORDER_ASSIGNED` and do not assign a raw row to either basis;
- one-row, more-than-two-row or physical-ordering exceptions are excluded and registered;
- no Lorda and Netta values are summed together;
- the XLSX is the controlling derivation source; API use is optional spot-check/exception resolution only.

### `MEM_Province_Region_MarketZone_Crosswalk.csv`

**Primary key:** `province_code` for the effective period.

Required fields:

`crosswalk_version`, `province_code`, `province_name`, `terna_province_name`, `region_code`, `region_name`, `terna_region_name`, `market_zone`, `active_from`, `active_to`, `source_id`, `review_status`.

Validation: 107 rows; exact-once mapping; canonical zone set only; `CNORD` never emitted.

### `Terna_Thermoelectric_Capacity_2024_Zone_Technology_CHP_Matrix.csv`

**Grain:** `year × capacity_basis × market_zone × Terna subcategory × CHP class`.

The matrix contains a complete seven-zone by fourteen-subcategory grid, including zero rows. Its national sum must equal the canonical capacity total exactly.

### `Terna_Thermoelectric_2024_Observed_Capacity_Factors.csv`

**Grain:** one declared aggregation level and key.

The only allowed formula is:

`observed_CF = production_netta_GWh / (capacity_netta_MW × 8.76)`

The record must state `calculation_status`. A CF is not calculated where the capacity/production perimeter is missing, the capacity is non-positive, or definitions do not align. The output is `QA / RECONCILIATION ONLY` and cannot overwrite future CF assumptions.

## Pending reconciliation contracts

### `Terna_GEM_Thermal_Reconciliation.csv`

Do not freeze until the complete GEM August-2026 release is archived.

Required fields:

`reconciliation_id`, `terna_record_or_control_id`, `terna_zone`, `terna_subcategory`, `terna_technology_normalized`, `terna_chp_class`, `terna_net_MW`, `gem_plant_id`, `gem_unit_id`, `gem_plant`, `gem_unit`, `gem_technology`, `gem_fuel`, `gem_chp_captive_role`, `gem_status`, `gem_gross_MW`, `commissioning_year`, `retirement_year`, `latitude`, `longitude`, `location_accuracy`, `owner`, `operator`, `source_or_wiki_url`, `gem_release`, `gem_raw_sha256`, `mapped_zone`, `gross_net_qualification`, `reconciliation_status`, `confidence`, `notes`.

Allowed reconciliation statuses include:

- `MATCHED_HIGH_CONFIDENCE`
- `MATCHED_PROVISIONAL`
- `TERNA_RESIDUAL_BELOW_GEM_THRESHOLD`
- `TERNA_NON_GEM_TECHNOLOGY`
- `GEM_UNIT_NOT_DIRECTLY_ALLOCATABLE_TO_TERNA_NET_MW`
- `GEM_GROSS_NET_NOT_DIRECTLY_COMPARABLE`
- `MATERIAL_2025_2026_CHANGE`
- `UNRESOLVED`

Terna net MW remains the capacity anchor. GEM gross MW may never be silently substituted.

### `MEM_Current_Thermal_Stack_2026.csv`

Required fields:

`generator_or_band_id`, `capacity_MW`, `capacity_basis`, `zone`, `technology`, `fuel`, `chp_flag`, `aggregation_level`, `plant_name`, `unit_name`, `gem_plant_id`, `gem_unit_id`, `status`, `commissioning_year`, `retirement_year`, `terna_control_id`, `gem_evidence_id`, `reconciliation_confidence`, `change_2025_H1_2026`, `change_capacity_MW`, `change_capacity_basis`, `model_treatment`, `notes`.

Allowed aggregation levels are `UNIT`, `PLANT`, and `ZONE_TECH_FUEL_COST_BAND`. A hybrid representation is required where unit evidence is incomplete. Small CHP/engine capacity and non-GEM technologies may remain Terna-controlled aggregate residuals.

Material 2025-H1 2026 changes stated only in GEM gross MW are recorded separately and do not automatically change Terna net MW.

### `MEM_Thermal_Fleet_Evolution_2026_2040_2050.csv`

Required fields:

`evolution_id`, `generator_or_band_id`, `scenario_id`, `source_year`, `target_year`, `action`, `capacity_before_MW`, `capacity_after_MW`, `capacity_basis`, `zone`, `technology`, `fuel`, `chp_flag`, `status_before`, `status_after`, `evidence_source_id`, `evidence_date`, `evidence_strength`, `assumption_class`, `reason`, `uncertainty`, `notes`.

Allowed actions:

`RETAIN`, `RETIRE`, `KNOWN_RETIREMENT`, `REFURBISH`, `CONVERT`, `CCS_RETROFIT_CANDIDATE`, `REPLACEMENT/NEW_BUILD`, `UNRESOLVED`.

Generic lifetime alone cannot create `KNOWN_RETIREMENT`. Project retention assumptions must remain distinct from source-supported continued capacity.

## Future controls and rejection rules

- 2040 approximately 55 GW is a national Netta thermoelectric envelope, not gas and not final `p_nom`.
- 2050 15.5136 GW is an energy-implied diagnostic only.
- approximately 30 GW is an independent adequacy benchmark.
- no 14.4864 GW capacity row or residual is allowed.
- 18.9449 GW is an external Slow validation benchmark only.
- no thermal reduction is mechanically imposed in High.
- no capacity increase is mechanically imposed in Slow before the deterministic outer-loop test.

## Network-loss rule

The DDS electricity-demand perimeter includes grid losses. Therefore:

- primary internal commercial Links must use `efficiency_pu=1.0`;
- a value below 1.0 is rejected unless the source annual demand target is first stripped of network losses and those losses are reconstructed endogenously;
- external-interface losses require a separate field stating whether the loss is already included in Italian demand/exchange accounting;
- no source demand loss and modelled internal transfer loss may be counted simultaneously.

## Status boundary

This addendum makes the Terna 2024 zonal technology baseline and current utilization diagnostics empirical. It does not declare the proposed 2026 physical fleet, 2040/2050 generator table or model-ready workbook empirically complete.

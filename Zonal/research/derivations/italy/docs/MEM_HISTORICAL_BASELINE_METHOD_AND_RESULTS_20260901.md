# SUPERSEDED - MEM Historical Capacity and Generation Baseline

This 2024/early-2019-2024 partial note is **SUPERSEDED_BY_2019_2024_CANONICAL_BASELINE**.

Use `MEM_HISTORICAL_BASELINE_METHOD_AND_RESULTS.md` in this directory. It contains the corrected exact-duplicate treatment, closed fuel-cell exceptions, wind split, storage layer, pumped-hydro overlap bridge, taxonomy audit and current freeze decision.

Controlling status: **HISTORICAL BASELINE INCOMPLETE**.

The remainder below is retained only as a dated audit trail and is not controlling.

# MEM Historical Capacity and Generation Baseline — Method and Results

Date: 2026-09-01  
Scope: Italian MEM seven-zone empirical history and 2024 detailed baseline  
Final gate status: **HISTORICAL BASELINE INCOMPLETE**

## Controlling boundary

The accepted v2.9 workbook remains unchanged. No v2.9.1 workbook is authorized by this phase.

Canonical zones are exactly:

`NORD`, `CNOR`, `CSUD`, `SUD`, `CALA`, `SICI`, `SARD`.

Every provincial observation is mapped through `MEM_Province_Region_MarketZone_Crosswalk.csv`. `CNORD` is never emitted.

## Immutable 2024 Download Center evidence

| Dataset | Original filename | Bytes | SHA-256 |
|---|---|---:|---|
| Bioenergy capacity | `Export-DownloadCenterFile-20260901-100929.xlsx` | 9,385 | `b6d8700e000e9f0766949a022c8e2a27eff41cb6cc14d3bc8005016e1820ea41` |
| Geothermal capacity | `Export-DownloadCenterFile-20260901-100933.xlsx` | 8,451 | `e4c22a28edf6cc6679bebed858a539d74284b4b9b20822b3d8ae1432613ea3a1` |
| Hydro capacity | `Export-DownloadCenterFile-20260901-100940.xlsx` | 9,457 | `0ecc2fa25a0a831f603789791dd0b19597b131af53dd7df9b9cb3dd2b73f4277` |
| Hydropower production by type | `Export-DownloadCenterFile-20260901-101019.xlsx` | 17,150 | `e5d0c67eba558f692fb66c9ba806356fdd3f4b2dfe7bb270ff5a463506640e9f` |
| Bioenergy production | `Export-DownloadCenterFile-20260901-101029.xlsx` | 9,656 | `94b6caceca9ca19e60f7471a8584019d9db75efb0bb11ac90fb52f954ab95b0a` |
| Geothermal production | `Export-DownloadCenterFile-20260901-101036.xlsx` | 8,369 | `addcfd865eff34850bad31e09e603db41c2524936ba402baa07875e039a51d91` |
| Thermoelectric produced heat | `Export-DownloadCenterFile-20260901-101113.xlsx` | 17,887 | `06a18e50214e6e5bd882819a1dde6ba1a0ee0457e0c6728727c12640a4e4a72c` |

The copies under `raw/terna/historical_2024/` are byte-identical. The originals were not edited.

## Normalization rules

1. Use explicit `Netta` capacity and production where the field exists.
2. Preserve raw labels, values, row lineage, source IDs, raw hashes and parser status.
3. Parse typed Excel numbers as numbers. For strings, detect decimal punctuation from the payload and reject ambiguity; punctuation is never stripped as a presumed thousands separator.
4. Preserve technology, fuel/source, CHP flag, storage flag and dispatchability class as separate dimensions.
5. Aggregate repeated official rows only at a documented visible grain and retain their source multiplicity.
6. Calculate observed CF as `NET generation GWh / (NET capacity MW × 8.76)` only where capacity and generation perimeters align.
7. Treat observed CF as QA and fleet characterization—not as a future dispatch constraint.

## Exact 2024 national results

| Technology/source control | NET capacity MW | NET generation GWh | Observed CF | Additivity rule |
|---|---:|---:|---:|---|
| Bioenergy source subset | 3,800.0923 | 15,699.033862 | 0.471600954 | Non-additive subset of thermoelectric population |
| Geothermal | 771.79 | 5,275.5733 | 0.780308627 | Additive outside the 2024 thermoelectric technology extract |
| Hydro renewable-source control | 19,324.42439 | 52,391.704319 | 0.309493729 | Additive annual hydro control; excludes pumping-discharge component |
| Solar PV | 37,002.141 | 35,398.16138 | 0.10915093 | Additive renewable technology |
| Wind aggregate | 12,959.811 | 22,087.791683 | 0.194546746 | Additive, but onshore/offshore not separated |

The bioenergy observed CF is close to 0.47, but it does not replace or re-derive the approved future `Bioenergy CF = 0.47` assumption.

## Thermoelectric/DDS perimeter

The current 2024 thermoelectric technology control is 60,331.82927 MW NET. Bioenergy is already inside that population and is not added. The 2024 thermoelectric extract has no geothermal-labelled technology; the separate 771.79 MW geothermal source control is therefore added for the DDS-comparable current perimeter:

`60,331.82927 + 771.79 = 61,103.61927 MW NET`.

Hydro remains outside the approximately 55-GW 2040 thermoelectric envelope.

Historical API evidence reveals a definition change that must remain year-specific:

| Year | Geothermal inside thermoelectric capacity extract MW | Separate geothermal source MW | Treatment |
|---:|---:|---:|---|
| 2019 | 767.19 | 767.19 | Source row non-additive; exact overlap |
| 2020 | 771.79 | 771.79 | Source row non-additive; exact overlap |
| 2021 | 771.79 | 771.79 | Source row non-additive; exact overlap |
| 2022 | 771.79 | 771.79 | Source row non-additive; exact overlap |
| 2023 | 0 | 771.79 | Source geothermal additive outside extract |
| 2024 | 0 | 771.79 | Source geothermal additive outside extract |

This prevents a false time-series discontinuity or double count.

## Hydro and pumping perimeter

The detailed hydric file reports:

| Hydric type | 2024 NET generation GWh |
|---|---:|
| Fluente | 24,915.2521 |
| Bacino | 14,613.094303 |
| Serbatoio (including eventual pumping) | 14,447.163499 |
| Detailed hydric total | 53,975.509902 |

The official renewable-source hydro control is 52,391.704319 GWh. The national difference is:

`53,975.509902 − 52,391.704319 = 1,583.805583 GWh`.

This difference is classified as a national statistical pumping-discharge perimeter proxy. It is not a physical pumped-storage unit allocation and not a PyPSA input. Signed zonal differences are retained only for QA because classification/allocation effects prevent a defensible zonal pumping split.

Until physical storage data are reconciled:

- renewable-source hydro is the additive annual energy control;
- Fluente/Bacino/Serbatoio rows are non-additive analytical detail;
- total Terna hydro MW is the capacity control;
- pumped discharge MW, charge MW, energy MWh and efficiency remain `REQUIRES DATA`;
- no generic hydro MW is added again as pumped-storage MW.

## Historical window and source stability

The six official Terna API families were archived for 2019–2025:

- thermoelectric capacity;
- thermoelectric production;
- renewable-source capacity;
- renewable-source production;
- hydric production by type;
- thermoelectric produced heat.

Actual non-empty coverage is 2019–2024. Every 2025 endpoint returned zero rows on 2026-09-01, so 2025 is not treated as an available consolidated annual year.

Terna documents annual renewable-source series and fields by region/province and identifies the source taxonomy; the hydric endpoint exposes `Bacino`, `Fluente` and `Serbatoio`; the heat endpoint exposes CHP plant types. See the official [Renewable Source Capacity](https://developer.terna.it/docs/apis_catalog/generation/Renewable_Source_Capacity), [Renewable Sources Production](https://developer.terna.it/docs/read/apis_catalog/generation/Renewable_Sources_Production), [Hydric](https://developer.terna.it/docs/apis_catalog/generation/Hydric), and [Thermoelectric Heat](https://developer.terna.it/docs/read/apis_catalog/generation/Thermoelectric_Heat) documentation.

The machine-readable historical outputs contain:

- 674 zonal capacity rows;
- 805 zonal generation rows;
- actual years 2019–2024;
- no backcast 2024 shares.

## Exceptions retained—not guessed

- 326 visible source-multiplicity groups are aggregated with lineage across thermoelectric capacity, renewable-source capacity and hydric type production.
- Hydric type production has 11 repeated visible `Serbatoio` groups per year; all official rows are retained.
- Thermoelectric production uses the two-row physical gross/net rule. Eight 2019–2022 fuel-cell CHP groups contain four values rather than two. Their NET generation and CF remain blank with `UNRESOLVED_GT2_PAIR_EXCEPTION`.
- Renewable-capacity API blanks are retained as blanks, not coerced to zero. Reported-value sums carry blank-observation counts and partial-status flags.
- Wind is retained as `WIND_AGGREGATE_UNSPLIT`; onshore/offshore allocation is not invented.

## Unchanged model guardrails

- Terna remains the authoritative capacity/energy control.
- GEM remains the plant/unit, fuel, status, age and coordinate reconciliation layer for oil/gas thermal assets.
- The 2040 55-GW control includes the relevant thermoelectric perimeter; it is not `CCGT + OCGT = 55 GW`.
- Internal MEM commercial Links remain lossless (`efficiency = 1.0`) because the DDS demand perimeter includes network losses, unless demand is first stripped of those losses and they are reconstructed endogenously.
- No full model build or annual dispatch run is authorized by this phase.

## Freeze decision

**HISTORICAL BASELINE INCOMPLETE**

The next canonical workbook successor is not authorized until at least the following are closed or explicitly accepted as controlled residuals:

1. pumped-hydro physical power/energy/efficiency and hydro type MW allocation;
2. BESS and other storage historical power/energy by zone;
3. thermal fuel/source reconciliation, primarily through GEM and authoritative fuel statistics;
4. wind onshore/offshore split;
5. eight 2019–2022 fuel-cell CHP gross/net pair exceptions;
6. review/acceptance of renewable-capacity blank semantics.

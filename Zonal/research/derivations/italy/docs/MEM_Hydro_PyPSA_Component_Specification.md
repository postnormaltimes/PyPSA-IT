# MEM Hydro PyPSA Component Specification

Status: **PHYSICAL ARCHITECTURE DECISION COMPLETE; NUMERICAL PARAMETERS PARTIAL**  
Phase: MEM 3.1, 2026-09-02  
Scope: later fixed-capacity seven-zone dispatch construction; no network is built or solved here.

## Permanent guardrail

`PHYSICAL_RESERVOIR_ENERGY != OPERATIONAL_ELECTRICAL_STORAGE_ENERGY != INSTALLED_GENERATION_CAPACITY`

Terna remains the numerical control for Italian NET capacity, annual generation and system-operational pumping metrics. The Catania/JRC/Zenodo inventory remains secondary physical, geographic and classification evidence. No open-inventory MW or reservoir-energy value becomes a solver input merely because it exists at plant level.

## Common water-energy convention

Reservoir, basin and pumped-storage components will use a water-energy bus and one state variable per physically shared reservoir system. With a water-potential-energy state, the hourly balance is conceptually:

`SOC[t] = retained_SOC[t-1] + natural_inflow[t] + eta_pump * grid_pumping[t] - turbine_input[t] - spill[t]`.

Electrical turbine output is:

`generation[t] = eta_turbine * turbine_input[t]`.

The chosen units must be consistent across the Store, inflow, pump Link and turbine Link. If an operational energy source states electrical energy deliverable at the grid rather than stored water-potential energy, it must be converted exactly once before assigning `Store.e_nom`. The turbine loss must not then be charged a second time.

All capacities are fixed and non-extendable. `Generator.p_nom_extendable`, `Link.p_nom_extendable` and `Store.e_nom_extendable` remain false.

## Physical architecture decisions

### Run-of-river

- PyPSA pattern: fixed `Generator` on the Italian zonal electricity bus.
- `p_nom`: Terna NET turbine capacity at the supported zone/class grain.
- Availability: exogenous hourly hydrological shape through `p_max_pu`.
- Curtailment: allowed and interpreted as unused/spilled water.
- Storage: no material Store in the primary representation.
- Grid charging: prohibited.

### Basin / pondage hydro

- PyPSA pattern: one water-energy `Store`, a forced exogenous inflow, a fixed turbine `Link` to the zonal electricity bus and an explicit non-negative spill path.
- Natural inflow enters the water state; it is not an optimizer-selectable fuel supply.
- The basin state represents short/intermediate intertemporal water flexibility.
- Grid charging is prohibited unless a specific asset is reclassified as mixed pumping.

### Conventional reservoir hydro

- PyPSA pattern: one naturally charged water-energy `Store`, forced exogenous inflow, fixed turbine `Link` and explicit spill path.
- The state may carry seasonal water energy when an active operating range is validated.
- Grid charging is prohibited.
- The open 5.748-TWh HDAM physical-energy sum is a plausibility/sensitivity view, not `e_nom`.

### Pure pumped-storage hydro

- PyPSA pattern: one shared `Store`, one fixed pump `Link` from the zonal electricity bus and one fixed turbine `Link` back to that bus.
- Natural inflow is zero or immaterial unless plant evidence establishes otherwise.
- Pump Link `p_nom` is electrical absorption at the grid-side input.
- Turbine Link `p_nom` must respect PyPSA's bus-0 input convention: if Terna NET MW controls electrical output, the water-side Link rating is `P_NET / eta_turbine` unless an equivalent output-side formulation is implemented and tested.
- The Terna approximately 53-GWh value is retained only at national operational-control grain pending an approved spatial allocation.

### Mixed reservoir plus pumping

- PyPSA pattern: **one shared water-energy Store** receiving both forced natural inflow and pumped water, with one fixed pump Link, one fixed turbine Link and one spill path.
- Natural inflow and grid pumping affect the same state.
- Never create an independent conventional-reservoir Store and a second PHS Store for the same plant/reservoir system.
- The 3,282.72439-MW mixed-pumping control remains inside the renewable-source hydro perimeter and is never added again to total hydro.

## Inflow and spillage

Natural inflow is exogenous. For Store-based hydro, the implementation must force the hourly inflow into the water-energy balance—for example with a fixed time-series injection whose minimum and maximum dispatch are identical—rather than letting the optimizer withhold water. An explicit spill variable or sink prevents reservoir overflow from making the problem infeasible.

The spill path must:

- be non-negative;
- have no electricity output;
- have zero or explicitly justified marginal cost;
- be fixed/non-extendable and large enough not to bind unintentionally;
- be reported separately in validation outputs.

For run-of-river, curtailed available output is the equivalent spill concept.

The preferred future hourly-shape candidate is the reproducible PyPSA-Eur hydro-profile workflow. It is an implementation layer, not the authority for Italian energy. The selected hydrology/weather year and every hourly series must be reconciled to Terna annual zonal/type generation controls. Historical capacity factor is not an hourly reservoir-availability substitute.

## State of charge

The architecture requires explicit choices for:

- initial SOC;
- terminal SOC;
- whether terminal equals initial;
- whether a cyclic annual condition is appropriate for the selected hydrological year;
- any minimum operating SOC;
- standing loss.

No choice is frozen in Phase 3.1. A cyclic annual condition is a later project candidate, not an automatic default: it can prevent free net energy across the year but may conceal an unrealistic seasonal starting state. An observed/derived initial state and terminal band is preferable if primary reservoir-level evidence becomes available.

Standing loss remains unset. A zero value may be tested later as an explicit project assumption for water reservoirs, but it is not inserted silently. Evaporation or other hydrological losses belong in the water balance when evidence supports them, not in Italy's commercial transmission-Link efficiency.

## Environmental and minimum-flow treatment

Minimum ecological release, irrigation obligations and other water constraints are not yet parameterized. If later included, they must be imposed on the water-release balance and sourced at the relevant basin/plant grain. They must not be approximated by a generic electrical minimum-output constraint without physical justification.

## Numerical parameter status

| Parameter | Current status | Controlling treatment |
|---|---|---|
| Total hydro NET MW | `CANONICAL_CONTROL` | 23,294.0 MW, QA/accounting control only |
| National RoR NET MW | `CANONICAL_CONTROL` | 6,239.0 MW from Terna Table 14 |
| National basin NET MW | `CANONICAL_CONTROL` | 5,019.9 MW from Terna Table 14 |
| National conventional-reservoir NET MW | `CANONICAL_CONTROL` | 4,782.8 MW, same-table reservoir total less PHS subset |
| National PHS discharge NET MW | `CANONICAL_CONTROL` | 7,252.3 MW |
| NORD RoR turbine `p_nom` | `MODEL_READY` | 5,227.9 MW NET |
| NORD basin turbine `p_nom` | `MODEL_READY` | 3,512.0 MW NET |
| NORD non-pumped reservoir turbine `p_nom` | `MODEL_READY` | 3,669.1 MW NET |
| Remaining six-zone hydro-class MW | `PROJECT_ASSUMPTION_REQUIRED` | Direct national/macroregional controls exist; seven-zone allocation does not |
| National PHS pump power | `CANONICAL_CONTROL` | approximately 6.4 GW at national system-maximum grain |
| Zonal PHS pump power | `PROJECT_ASSUMPTION_REQUIRED` | two transparent candidates; neither approved |
| National PHS operational energy | `CANONICAL_CONTROL` | approximately 53 GWh, national only |
| Zonal/plant PHS operational energy | `PROJECT_ASSUMPTION_REQUIRED` | three 53-GWh allocation candidates; none canonical |
| PHS round-trip efficiency | `MODEL_CANDIDATE` | Terna technology range 70-75% |
| Separate pump/turbine efficiencies | `PROJECT_ASSUMPTION_REQUIRED` | symmetric square-root split is only a sensitivity candidate |
| Conventional active reservoir energy | `PROJECT_ASSUMPTION_REQUIRED` | active operating range not established; open HDAM energy remains physical evidence |
| Hourly inflow shapes | `MODEL_CANDIDATE` | PyPSA-Eur/reproducible hydrology candidate; year and annual scaling unresolved |
| Initial/terminal SOC and standing loss | `PROJECT_ASSUMPTION_REQUIRED` | later approval required |

## PHS allocation candidates

`MEM_PHS_Operational_Energy_Allocation_Candidates.csv` contains three alternatives, each reconciling exactly to 53,000 MWh and each marked `model_input=false`:

1. Terna zonal pumped-discharge-power shares;
2. open HPHS physical-reservoir-energy shares, normalized only as a sensitivity;
3. Terna's 75% concentration in four principal pure-PHS plants, with the threshold/map and operator evidence used to identify the candidate set Entracque-Chiotas, Edolo and Roncovalgrande in `NORD` and Presenzano in `CSUD`; Terna does not enumerate the four in the reviewed sentence, so the identity inference plus the within-group and residual splits remain explicit project assumptions.

Method 3 is the preferred candidate for later user approval because it preserves Terna's strongest published concentration statement. It is not canonical because Terna does not publish plant-specific usable GWh in the reviewed evidence.

## Required solver-input gates

Before hydro input freeze, MEM still needs:

- an approved PHS operational-energy allocation;
- an approved zonal PHS pump-power allocation;
- an approved efficiency point and loss split within the Terna 70-75% RTE range, or plant-specific evidence;
- six-zone RoR/basin/reservoir NET-MW allocation constrained by Terna controls;
- active/usable reservoir-energy parameters;
- a selected inflow/weather year and annual energy reconciliation;
- initial and terminal SOC policy;
- any standing loss and environmental release constraints.

These gaps keep `HYDRO_MODEL_PARAMETER_STATUS = PARTIAL` while leaving `HISTORICAL_TECHNOLOGY_BASELINE_STATUS = FROZEN`.

## Source anchors

- [Terna 2024 generation-plant statistics, Table 14](https://download.terna.it/terna/03_IMPIANTI%20DI%20GENERAZIONE_8dec285ed22347a.pdf)
- [Terna 2025 Development Plan, section 3.5.1](https://download.terna.it/terna/Terna_Piano_Sviluppo_2025_Stato_sistema_elettrico_scenari_energetici_8dd62ec4bbb9f75.pdf)
- [Terna storage-technology study](https://download.terna.it/terna/Studio_tecnologie_di_accumulo_8db9511fbdd7601.pdf)
- [PyPSA-Eur hydro attachment workflow](https://github.com/PyPSA/pypsa-eur/blob/master/scripts/add_electricity.py)

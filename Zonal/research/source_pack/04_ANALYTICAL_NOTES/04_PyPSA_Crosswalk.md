# 04 — Crosswalk verso PyPSA e PyPSA-Eur

## 1. Versione e principio di prevalenza

Il crosswalk è stato verificato il **19 luglio 2026** contro la documentazione ufficiale corrente di PyPSA e PyPSA-Eur.

La documentazione PyPSA-Eur corrente indica una configurazione di default **v2026.02.0**, ma il branch/commit del progetto separato **PyPSA-Eur CLI** deve prevalere sulla documentazione generale. Il presente file definisce quindi la semantica degli input; non presume il nome definitivo dei file o delle regole Snakemake da modificare.

## 2. Formulazione di base raccomandata

- **Bus:** una zona di mercato italiana o estera aggregata.
- **Load:** domanda oraria per zona, eventualmente separata per nuovi carichi/settori.
- **Generator:** generazione convenzionale, VRE, run-of-river e load shedding.
- **Store + Link:** batterie e storage con potenza ed energia indipendenti.
- **StorageUnit:** alternativa per storage con rapporto potenza/energia fisso e per alcuni impianti idroelettrici.
- **Link:** corridoi interzonali, interconnector, conversioni e charge/discharge.
- **Carrier:** tassonomia e fattori emissivi.
- **Capacità:** fisse e non estendibili nel caso base.

## 3. Formula del costo marginale termoelettrico

```text
marginal_cost [EUR/MWh_el]
  = fuel_price [EUR/MWh_th] / efficiency [MWh_el/MWh_th]
  + co2_price [EUR/tCO2] * emission_factor [tCO2/MWh_th] / efficiency
  + variable_om [EUR/MWh_el]
  + other_variable_costs [EUR/MWh_el]
```

Controlli obbligatori:

- fuel price e fattore emissivo devono usare lo stesso denominatore termico;
- il costo CO₂ non deve essere applicato una seconda volta tramite un altro meccanismo;
- CAPEX e fixed O&M non devono entrare nel merit order del caso base;
- il costo marginale può essere time-varying, ma il caso base userà valori di scenario annuali salvo evidenza contraria.

## 4. Crosswalk completo

| ID | Blocco | Concetto | Componente | Statico/serie | Campo/i | Unità | Trattamento dispatch-only | Trasformazione | Decisione | Controllo QA | Stato |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| MAP-001 | Scenario | Snapshot index | Network | Time series | snapshots | datetime | Full-year hourly base | Create timezone-consistent datetime index | D04 | 8760/8784 rows and no gaps | Ready for data extraction |
| MAP-002 | Scenario | Snapshot weights | Network | Time series | snapshot_weightings | hours | Set to 1 for hourly full year | Assign objective/generator/store weights consistently | D04 | Annual sums equal unweighted hourly sums | Ready for data extraction |
| MAP-003 | Geography | Italian/foreign market nodes | Bus | Static | name; carrier; country; x; y | text/degrees | Fixed topology | Create buses and mapping table | D01;D02;D03 | Every asset maps to one valid bus | Ready for data extraction |
| MAP-004 | Demand | Fixed hourly demand | Load | Time series | bus; p_set | MW | Exogenous inelastic load | Scale normalized profiles to annual zonal totals | D06;D08 | Sum p_set × weights matches annual demand | Ready for data extraction |
| MAP-005 | Demand | Sector-specific/new loads | Load | Static + time series | carrier; bus; p_set | MW | Separate components where shape/flex differs | Build one Load per zone-sector | D07 | Sector sums reconcile to total | Ready for data extraction |
| MAP-006 | Demand | Load shedding | Generator | Static + output | bus; carrier; p_nom; marginal_cost; p_nom_extendable=False | MW; EUR/MWh | Virtual generator with high finite cost | p_nom at least zonal peak; cost=VOLL | D28 | Zero dispatch in normal cases; EENS reported | Ready for data extraction |
| MAP-007 | Generation | Fixed dispatchable capacity | Generator | Static | bus; carrier; p_nom; p_nom_extendable=False | MW | Capacity exogenous | Load harmonized capacity by zone/cohort | D09 | Sum by tech/zone equals scenario totals | Ready for data extraction |
| MAP-008 | Generation | Variable renewable capacity | Generator | Static + time series | p_nom; p_max_pu; p_min_pu=0; marginal_cost | MW; p.u.; EUR/MWh | Curtailable variable generator | Use atlite/PyPSA profiles and scenario p_nom | D05;D14 | 0<=p_max_pu<=1; annual CF plausible | Ready for data extraction |
| MAP-009 | Generation | Conventional availability | Generator | Time series/static | p_max_pu | p.u. | Derating/outage constraint | Apply availability once | D11 | Values in [0,1]; no capacity double-derating | Ready for data extraction |
| MAP-010 | Generation | Minimum stable output/must-run | Generator | Time series/static | p_min_pu | p.u. | Use only with evidence; without UC means must-run | Assign by cohort/period | D10 | 0<=p_min_pu<=p_max_pu | Ready for data extraction |
| MAP-011 | Generation | Efficiency | Generator or Link | Static/time series | efficiency | p.u. | Used in cost and emissions accounting | Assign by technology/cohort | D12 | 0<efficiency<=1; consistent with marginal cost | Ready for data extraction |
| MAP-012 | Generation | Marginal production cost | Generator or Link | Static/time series | marginal_cost | EUR/MWh_el | Primary merit-order parameter | fuel/eta + CO2*EF/eta + VOM + other | D12;D13;D14 | Recalculate and benchmark gas-vs-PUN relation | Ready for data extraction |
| MAP-013 | Generation | Fuel and CO2 emissions | Carrier | Static | co2_emissions | tCO2/MWh_th | Metadata/global constraints; carbon price usually precomputed in marginal cost | Map each generator to fuel carrier | D12 | No double carbon charging | Ready for data extraction |
| MAP-014 | Generation | Annual energy limits | Generator | Static | e_sum_min; e_sum_max | MWh | Only for resource/contract constraints | Set for hydro/biomass/waste only if justified | D15 | Binding values reconcile with annual energy evidence | Ready for data extraction |
| MAP-015 | Generation | Unit commitment | Generator | Static + variables | committable; start_up_cost; shut_down_cost; stand_by_cost; min_up_time; min_down_time | bool; EUR; snapshots | Sensitivity, not base case | Enable on sufficiently disaggregated thermal cohorts | D10 | Compare LP and MILP outputs; no unsupported data defaults | Ready for data extraction |
| MAP-016 | Generation | Ramp constraints | Generator | Static/time series | ramp_limit_up; ramp_limit_down; ramp_limit_start_up; ramp_limit_shut_down | p.u./snapshot | Optional/base only if robust | Convert MW/min or %/min to per snapshot | D10 | Check units against hourly resolution | Ready for data extraction |
| MAP-017 | Hydro | Run-of-river | Generator | Static + time series | p_nom; p_max_pu; marginal_cost | MW; p.u. | Curtailable inflow-limited generation | Convert hydrological profile to p_max_pu | D15 | Annual generation and seasonal shape plausible | Ready for data extraction |
| MAP-018 | Hydro | Reservoir with coupled power-energy | StorageUnit | Static + time series | p_nom; max_hours; inflow; efficiency_dispatch; state_of_charge_initial; cyclic_state_of_charge | MW; h; MW; p.u.; MWh | Use only if fixed power-energy ratio is acceptable | Convert energy capacity to max_hours | D15;D16 | p_nom*max_hours equals energy capacity | Ready for data extraction |
| MAP-019 | Hydro | Reservoir/pumped hydro with independent power-energy | Store + Links | Static + time series | Store.e_nom/e_initial/e_cyclic; Link.p_nom/efficiency/p_min_pu/p_max_pu | MWh; MW; p.u. | Preferred when charge/discharge power and energy are independent/asymmetric | Auxiliary hydro bus plus turbine/pump Links | D15;D16 | Energy/power and round-trip efficiency reconcile | Ready for data extraction |
| MAP-020 | Storage | BESS energy capacity | Store | Static | bus; carrier; e_nom; e_nom_extendable=False; e_initial; e_cyclic; standing_loss | MWh; p.u. | Fixed energy capacity | Create auxiliary battery bus/store by zone-duration class | D17;D18;D19 | e_nom totals and SOC closure | Ready for data extraction |
| MAP-021 | Storage | BESS charge/discharge power | Link | Static + time series | bus0; bus1; p_nom; p_nom_extendable=False; efficiency; p_min_pu; p_max_pu; marginal_cost | MW; p.u.; EUR/MWh | Separate charge and discharge Links | Use asymmetric ratings if evidence supports | D17;D18 | p_nom totals and round-trip efficiency | Ready for data extraction |
| MAP-022 | Storage | StorageUnit alternative | StorageUnit | Static + time series | p_nom; max_hours; efficiency_store; efficiency_dispatch; standing_loss; cyclic_state_of_charge | MW; h; p.u. | Alternative only for fixed duration and symmetric power | Use when source only supports coupled p/e | D17 | Compare with Store+Links representation | Ready for data extraction |
| MAP-023 | Transmission | Zonal corridor capacity | Link | Static + time series | bus0; bus1; p_nom; p_min_pu; p_max_pu; efficiency | MW; p.u. | Transport model base | Use market transfer capacity, direction and seasonality | D20;D21;D23 | Flows never exceed directional limits | Ready for data extraction |
| MAP-024 | Transmission | Asymmetric corridor | Two Link components | Static + time series | separate p_nom/p_max_pu by direction | MW | Use when import/export limits differ | Create A→B and B→A Links | D21 | Directional capacities reproduce source table | Ready for data extraction |
| MAP-025 | Transmission | Physical nodal sensitivity | Line/Link | Static | s_nom; x; r; length or Link equivalents | MVA; p.u.; km | Not base; optional nodal sensitivity | Use existing PyPSA-Eur network | D01 | Compare zonal aggregate flows/capacities | Ready for data extraction |
| MAP-026 | Foreign systems | Endogenous foreign zones | Bus + Generator + Load + Storage + Link | Static + time series | multiple | MW/MWh/EUR | Preferred if computationally feasible | Use PyPSA-Eur subset/aggregation | D03 | Foreign balances and border flows close | Ready for data extraction |
| MAP-027 | Foreign systems | Exogenous import supply | Generator at foreign/border bus | Time series | p_nom; p_max_pu; marginal_cost | MW; p.u.; EUR/MWh | Sensitivity/alternative to endogenous foreign system | Set availability and external price profile | D03 | No import beyond NTC; price profiles documented | Ready for data extraction |
| MAP-028 | Market | Zonal price | Bus output | Time series | marginal_price | EUR/MWh | Primary price output | Extract by snapshot/zone | D29 | Compare historical backcast to GME zonal prices | Ready for data extraction |
| MAP-029 | Market | National price proxy | Post-processing | Time series | weighted average of Bus.marginal_price | EUR/MWh | Not a native PyPSA component | Compute after solve | D29 | Weights sum to one and reconcile volumes | Method to finalise |
| MAP-030 | Market | Curtailment | Post-processing | Time series | p_nom*p_max_pu - Generator.p | MW/MWh | Output metric | Apply snapshot weights | D14 | No negative curtailment beyond tolerance | Ready for data extraction |
| MAP-031 | Market | Storage operation | Store/StorageUnit/Link output | Time series | e; state_of_charge; p; p0; p1 | MWh; MW | Output metric | Aggregate by zone/class | D18 | SOC and energy balance close | Ready for data extraction |
| MAP-032 | Market | Interzonal flow/congestion | Link output | Time series | p0; p1; mu_upper; mu_lower | MW; EUR/MW | Output metric | Calculate saturation and congestion rents | D20 | Flow and price spreads consistent | Ready for data extraction |
| MAP-033 | Market | Adequacy/EENS | Load-shedding Generator output | Time series | p | MW/MWh | Output metric | Sum with weights; count hours | D28 | Zero/limited EENS in adequate scenarios | Ready for data extraction |
| MAP-034 | Metadata | Capital costs and FOM | Generator/Link/Store metadata | Static | capital_cost; overnight_cost; fom_cost; lifetime; discount_rate | EUR/MW; years; p.u. | Keep out of base dispatch objective or store sidecar | Load for context/future expansion only | D35 | Objective unaffected by fixed cost values in base | Ready for data extraction |
| MAP-035 | Configuration | Scenario/config overrides | PyPSA-Eur YAML | Static | run; scenario; snapshots; electricity; lines; links; load; costs; solving | mixed | Use custom config/scenario overrides, not ad hoc edits | Map workbook outputs to repo-specific override files | D36 | Config diff is explicit and version-controlled | Ready for data extraction |

## 5. Convenzioni critiche PyPSA

### Generator

- `p_nom` limita il dispatch.
- `p_max_pu` rappresenta disponibilità o derating.
- `p_min_pu` può introdurre must-run se `committable=False`.
- `marginal_cost` è il costo di produrre 1 MWh.
- `efficiency` è output elettrico diviso input di energia primaria.
- unit commitment richiede `committable=True` e dati aggiuntivi.

### StorageUnit

È adatto quando potenza ed energia sono legate da `max_hours`:

```text
e_nom = p_nom × max_hours
```

Supporta inflow e spillage e dispone di efficienze separate di carica/scarica.

### Store + Link

È preferibile quando:

- MW e MWh devono essere indipendenti;
- carica e scarica hanno potenze diverse;
- servono efficienze asimmetriche;
- le classi di durata devono essere rappresentate esplicitamente.

Il `Store` non limita la potenza: i limiti di carica e scarica devono essere imposti con Link separati.

### Link zonali

- `p_nom` è il limite di potenza in unità del `bus0`;
- `p_min_pu` può essere negativo;
- per limiti direzionali asimmetrici è più trasparente usare due Link;
- `efficiency` può rappresentare le perdite di trasferimento;
- il parametro deve essere la capacità resa disponibile al mercato, non semplicemente il rating nominale dell'opera.

## 6. Input esclusi dall'obiettivo base

I seguenti dati saranno raccolti ma non dovranno modificare il dispatch a capacità fisse:

- CAPEX;
- fixed O&M;
- WACC;
- vita tecnica, salvo controllo dello status;
- costo totale di scenario;
- investimento complessivo di rete.

## 7. Output minimi obbligatori

- prezzi marginali orari zonali;
- proxy nazionale/PUN con metodologia esplicita;
- generazione per tecnologia e zona;
- flussi interzonali e import/export;
- ore di congestione e price separation;
- curtailment;
- carica, scarica e SOC degli accumuli;
- load shedding, EENS e ore di scarsità;
- tecnologia marginale;
- bilancio energetico;
- shadow prices dei vincoli principali.

## 8. Riferimenti tecnici ufficiali

- PyPSA Generator: https://docs.pypsa.org/latest/user-guide/components/generators/
- PyPSA StorageUnit: https://docs.pypsa.org/latest/user-guide/components/storage-units/
- PyPSA Store: https://docs.pypsa.org/latest/user-guide/components/stores/
- PyPSA Link: https://docs.pypsa.org/latest/user-guide/components/links/
- PyPSA Load: https://docs.pypsa.org/latest/user-guide/components/loads/
- PyPSA electricity-market examples: https://docs.pypsa.org/latest/examples/simple-electricity-market-examples/
- PyPSA-Eur configuration: https://pypsa-eur.readthedocs.io/en/latest/configuration.html

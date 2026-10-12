# Scenario governance

The final release selects exactly six cases: 2040 final_v3 Slow/Base/High and 2050 final_v6 Slow/Base/High. `config/publication.yaml` records the live result definitions; `config/publication_research_release.yaml` points the same figure definitions at public result tables. The latter is a data-routing profile, not a changed model methodology.

Capacities, demand, fuel/carbon assumptions, wind/hydro controls, P2X construction and interface limits remain accepted inputs. Model operation is fixed-capacity, with no endogenous expansion. All selected cases remain explicit, manually executable, with authorization separate from analytical review. Configuration schema identities and historical preparation statuses are retained for compatibility.

The final execution router retains reference LP → physical UC MILP → fixed-commitment price LP and same-case verification gates. Gurobi settings remain version 13.0.3, Threads=1, Seed=0. The public input derivatives change only portable metadata and dependent integrity hashes, never numerical arrays. Original/public hashes are recorded together.

The publication preserves chronology/version distinctions: 2040 v2 was never executed; 2050 v5 reinforcement precedes v6 external-cost normalization. Physical shedding comes from UC, price scarcity from LP prices. Six saved final QA records are PASS. Completed figure production and numerical QA do not substitute for final editorial publication approval.

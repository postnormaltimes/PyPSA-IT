# Scenario controls and status

The current final_v1 family is defined by config/final_methodology_execution_v1.yaml, accepted final closure controls, frozen static/runtime inputs and calibrated thermal-unit parameters. The original static package remains authoritative for capacities, carriers, storage controls, annual demand and directional interfaces. Final transformations do not permit capacity expansion.

| Model family | Inputs | Results |
| --- | --- | --- |
| final_v1 2040 Slow/Base/High | Canonical prepared UC/reference pairs | Technical QA passed; analytical review recorded separately |
| final_v1 2050 Slow/Base/High | Canonical prepared pairs | Prepared final pairs; explicit manual execution authorized; analytical acceptance remains separate |
| Prior UC2 | Calibrated predecessor | Historical solved evidence |
| Original dispatch / pre-P2X | Earlier diagnostic families | Methodological evidence only |

Authorization, input integrity and result acceptance are separate controls. The production command explicitly selects final_v1. External-price datasets are frozen at the Stage-A/Stage-B boundary; no current scenario requires rerunning the reduced external model.

Research assumptions are closed for execution. Missing implementation fields must be recovered and reconciled with accepted derivations rather than replaced by unsupported assumptions. Material changes to capacities, annual demand, fuels/carbon, hydro/PHS, network contracts or price formation require documented methodological review.

Immutable receipt and contract identifiers retain their original technical names for traceability. Their descriptive text records the state at creation; only current configuration authorizes execution. Historical 2050 results are not interchangeable with final_v1 outputs.

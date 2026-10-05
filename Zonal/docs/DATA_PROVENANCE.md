# Data provenance

inputs/manifests/data_package.csv defines executable and optional presentation artifacts by logical identity, role, destination, SHA256 and version. Accepted derived inputs are distributed directly to preserve the exact model realization. Original evidence is preserved separately. Portable metadata derivatives retain original source identities and are indexed in inputs/manifests/artifact_provenance.csv. Original source hashes describe the source evidence; derived hashes describe the distributed files.

| Layer | Canonical artifact / derivation | Storage |
| --- | --- | --- |
| Italian static model | pre_pypsa_inputs; capacity-master and source-derived controls | Repository |
| Horizon demand, costs and technical availability | runtime_inputs/accepted and accepted_2050 | Small controls in repository; hourly data package |
| External prices | stage_a_results/price_handoff/2040 and 2050 | Small price datasets and immutable boundary manifests in repository |
| P2X / thermal units | Scenario controls, unit archetypes, accepted full-precision child crosswalk | Repository; deterministic reconstruction |
| Solar / final wind | Zonal solar and horizon-specific native-cell effective profiles | Data package |
| Final hydro | Zonal natural inflow, state census, water mapping, accessible/bypass controls | Controls in repository; hourly data package |
| Prepared final networks | networks/unsolved/final_methodology_v1 | Versioned data package |
| Research | Source registers, extraction workbooks, capacity/scenario and operating derivations | Lightweight material in repository; larger evidence separately preserved |

The accepted 2040 and 2050 B10 boundary datasets freeze the reduced external-market model's prices. Original source selection and hashes are preserved; missing historical intermediate packages do not invalidate these accepted executable datasets. Bootstrap restores exact runtime aliases from the boundary files.

The synchronized 2019 chronology is retained, while final wind/hydro replacements and horizon magnitudes remain explicit. Raw weather, upstream calibration machinery, historical ZIP snapshots and temporary conversion caches are not execution dependencies. Original native-cell arrays remain evidence, stored outside their former temporary directory.

Optional presentation source/geography is independently indexed. It cannot prevent core reporting. Credentials, development environments and licence files are excluded from all distributed artifacts.

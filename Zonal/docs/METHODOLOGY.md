# Modelling methodology

Italy is represented by seven bidding-zone buses with fixed generation and storage capacities, exogenous annual demand, accepted 2019 hourly profiles and directional commercial electricity interfaces. There is no endogenous expansion or intra-zonal physical transmission model.

Thermal operation uses the accepted synthetic-unit construction, efficiency heterogeneity, operating-cost/fuel/carbon controls, technical availability and unit-commitment parameters. P2X electrical consumption is modeled explicitly. Renewable generation, conventional hydro, pure/mixed PHS and batteries retain their accepted energy, power and efficiency controls. Mixed PHS has one shared water state for natural inflow and pumping; charging/discharging energy is not duplicated.

The continuous reference LP is followed by the physical UC MILP. A fixed-commitment LP recovers prices conditional on the verified commitment result. Canonical physical quantities use UC operation; pricing-LP primal operation is not substituted for it. Dual prices and water values retain the accepted objective-weight convention and verification contract.

Primary generation excludes imports, BESS/PHS discharge and diagnostic shedding. Electrical balances retain storage charging, P2X and signed exchanges separately. National hourly prices are a modeled GME-style demand-weighted index using rigid plus P2X electricity consumption; annual/monthly purchasing prices additionally use physical represented duration and electricity volume. The time mean of the hourly index is a different statistic.

External-market support and prices are synthetic accepted boundary abstractions. In 2050 final_v6 only the exceptional EUR 15,000/MWh external-generator input is normalized to EUR 400/MWh. Italian load-shedding VOLL stays EUR 15,000/MWh. This is not an Italian price cap or a re-estimation of historical market prices.

Marginal price-setting regimes use the accepted lexicographic method with validated same-LP roots. Missing validated roots return WAITING; there is no automatic discovery, solve or substitution by the abandoned economic offer-stack approach. Publication reuses validated categorical evidence and preserves its source identities.

See [version evolution](FINAL_VERSION_EVOLUTION.md), [data provenance](DATA_PROVENANCE.md), [scenario governance](SCENARIO_GOVERNANCE.md) and [limitations](LIMITATIONS.md). Research/source registers remain available; raw upstream datasets retain their own access and reuse conditions.

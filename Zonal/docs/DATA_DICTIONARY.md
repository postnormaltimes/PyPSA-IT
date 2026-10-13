# Numerical data dictionary

The authoritative machine-readable dictionary is installed at `data/research_results/DATA_DICTIONARY.json`. The repository manifest links all datasets to case, source, authority, source/export SHA-256, columns, units and transformation.

Core CSVs contain 8,760 hourly UTC snapshots from 2019-01-01 00:00 through 2019-12-31 23:00. Timestamps are naive UTC, with no DST adjustment. Seven zone identifiers are NORD, CNOR, CSUD, SUD, CALA, SICI, SARD. CNORD appears only as a documented display alias; NATIONAL rows are additional aggregates, not an eighth zone.

Prices are EUR/MWh, electricity power MW, energy MWh or explicitly labelled GWh/TWh, installed capacity MW/GW. Raw Store states use internal MWh; water-state energy must not be confused with turbine electrical energy. Positive charging is withdrawal; discharging is separate supply. Net imports are positive inward. Signed net interface flow is positive from bus_a/from_bus to bus_b/to_bus; reverse values are negative. Directional raw link terminal powers retain the named terminal convention.

`hourly_generation_dispatch.csv` includes the accepted technology display columns, rigid and P2X demand, BESS/PHS charging, net imports and physical shedding. Storage discharge, imports and shedding are not primary generation. `hourly_national_price_index.csv` exposes the demand denominator, physical duration, electricity purchasing weight and price-weighted numerator. It preserves full precision and reconciles to the accepted national index and annual purchasing price.

Annual, monthly and more granular Parquet tables retain original column definitions and labelled units. QA/receipt/manifests are evidence records, not additional electricity or price observations. Consult their referenced source role when comparing quantities. Published data are modeled outcomes; there is no interpolation, price clipping or fabricated v2 result series.

# BLK-008 — Prezzo CO2 2050

## Stato

**BLK-008: CLOSED**

## Scenari adottati

| Caso | EUR2025/tCO2 | Anchor |
| --- | ---: | --- |
| Low | 175 | TYNDP 2024 / IEA APS: 168 EUR/t |
| Base | 400 | VIIEW Fit55/removals: 380 EUR/t |
| High | 550 | VIIEW Fit55_sup50: circa 550 EUR/t |
| Extreme | 1000 | VIIEW no/weak-removals cases: 1000 EUR/t |

Il valore centrale è una assunzione di scenario, non una previsione puntuale del prezzo EUA.

## Selezione della base

La base 400 EUR2025/tCO2 è ancorata a una proiezione diretta EU ETS di 380 EUR/t nel 2050 in uno scenario Fit55 con piena valorizzazione delle rimozioni. Il valore viene arrotondato per evitare falsa precisione monetaria.

Il TYNDP 2024 adotta 168 EUR/t nel 2050 su base IEA APS. È utilizzato come caso low e arrotondato a 175 EUR2025/t.

Il caso high 550 EUR/t rappresenta una disponibilità parziale delle rimozioni. Il caso 1000 EUR/t è stress-only.

## Merit order 2050

### CCGT

- Low: 101.023 EUR/MWh
- Base: 175.273 EUR/MWh
- High: 224.773 EUR/MWh
- Extreme: 373.273 EUR/MWh

### OCGT

- Low: 139.518 EUR/MWh
- Base: 243.122 EUR/MWh
- High: 312.192 EUR/MWh
- Extreme: 519.401 EUR/MWh

## Formula

```text
marginal cost
= gas price / efficiency
+ CO2 price × emission factor / efficiency
+ VOM
```

Assunzioni 2050:

- gas: 22.7578 EUR2025/MWh_th;
- emission factor: 0.198 tCO2/MWh_th;
- CCGT efficiency: 0.6;
- OCGT efficiency: 0.43.

## Monetary-base treatment

Published anchors and adopted values are stored separately. The sources do not consistently disclose a directly comparable price base in the extracted tables. The workbook therefore uses rounded EUR2025 engineering assumptions rather than an artificial high-precision inflation conversion.

## Outcome

All external research blockers are closed. The model-input specification is ready for implementation in the PyPSA-Eur project configuration.

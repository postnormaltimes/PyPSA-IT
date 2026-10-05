# 12 — Registro dei gap e delle fonti esterne

## 1. Principio

Ogni parametro mancante è registrato con:

- tecnologia;
- criticità per il dispatch;
- criticità per il report;
- fonte esterna candidata;
- conversione;
- priorità;
- fase di risoluzione.

Nessun valore esterno candidato è stato adottato automaticamente nella Fase 3D.

## 2. Registro completo

| ID | Tecnologia | Parametro | Criticità dispatch | Fonte candidata | Priorità | Risoluzione |
| --- | --- | --- | --- | --- | --- | --- |
| G3D-001 | CCGT fleet cohorts | Efficiency by cohort and year | Critical | Plant-level data / pinned technology-data / JRC | P0 | Phase 4 |
| G3D-002 | OCGT | Efficiency and VOM | Critical | Pinned technology-data / DEA / JRC | P0 | Phase 4 |
| G3D-003 | Gas fleet | Availability and forced outage | Critical | ENTSO-E Transparency / Terna / plant data | P0 | Phase 4 |
| G3D-004 | Gas fleet | Minimum stable output | High | DEA / JRC / plant data | P1 | Before UC sensitivity |
| G3D-005 | Gas fleet | Ramp rates | Medium | DEA / JRC / plant data | P1 | Before UC sensitivity |
| G3D-006 | Gas fleet | Startup and shutdown costs | Medium | DEA / JRC / plant data | P1 | Before UC sensitivity |
| G3D-007 | Coal/oil residual units | Efficiency, VOM and emissions | High | Pinned technology-data / JRC / ISPRA | P1 | Phase 4 |
| G3D-008 | Solar/wind | Hourly availability profiles | Critical | PyPSA-Eur weather pipeline / PECD / ERA5 | P0 | Phase 4 |
| G3D-009 | Solar/wind | Marginal bid / negative offers | Medium | GME/ARERA market evidence | P2 | Phase 4 |
| G3D-010 | Biomass | Fuel cost, efficiency, VOM and emissions | High | Pinned technology-data / JRC / ISPRA | P1 | Phase 4 |
| G3D-011 | Geothermal | VOM, availability and minimum generation | High | JRC / DEA / Italian plant data | P1 | Phase 4 |
| G3D-012 | Li-ion BESS | One-way charge/discharge efficiency | Critical | Internal 90% RTE plus pinned technology-data | P0 | Phase 4 |
| G3D-013 | Li-ion BESS | Standing loss | High | Pinned technology-data / DEA storage catalogue | P1 | Phase 4 |
| G3D-014 | Li-ion BESS | Variable degradation/cycling cost | Medium | DEA / JRC / battery studies | P2 | Sensitivity |
| G3D-015 | Pumped hydro | One-way efficiencies | Critical | Pinned technology-data / plant data / Terna storage study | P0 | Phase 4 |
| G3D-016 | Reservoir hydro | Energy capacity and inflows | Critical | ENTSO-E / JRC hydro database / PyPSA-Eur | P0 | Phase 4 |
| G3D-017 | Run-of-river | Hourly inflow/profile | Critical | PyPSA-Eur / ENTSO-E / climate data | P0 | Phase 4 |
| G3D-018 | New nuclear / SMR | Efficiency, fuel and VOM | Critical for nuclear branch | Pinned technology-data / JRC / IEA / DEA | P0 | Phase 4 |
| G3D-019 | New nuclear / SMR | Availability, refuelling and minimum load | Critical for nuclear branch | IEA / JRC / vendor or regulator data | P0 | Phase 4 |
| G3D-020 | Gas CCGT with CCS | Capture rate | Critical for CCS branch | DEA CCUS catalogue / JRC | P0 | Phase 4 |
| G3D-021 | Gas CCGT with CCS | Efficiency penalty | Critical for CCS branch | DEA CCUS catalogue / JRC | P0 | Phase 4 |
| G3D-022 | Gas CCGT with CCS | Variable capture, transport and storage cost | Critical for CCS branch | S07 + DEA/JRC | P0 | Phase 4 |
| G3D-023 | Interzonal links | Loss factors | Medium | Terna / ENTSO-E / PyPSA-Eur | P2 | Sensitivity |
| G3D-024 | Interzonal and foreign links | Availability / outage | High | ENTSO-E / Terna | P1 | Phase 4 |
| G3D-025 | System | Value of lost load / load shedding cost | Critical | ARERA / ACER / ENTSO-E adequacy methodology | P0 | Phase 4 |
| G3D-026 | System | Price cap, floor and negative-price rules | High | GME / EU market rules | P1 | Phase 4 |
| G3D-027 | All thermal | 2050 gas and CO2 scenario prices | Critical | EU Commission / TYNDP / scenario source | P0 | Phase 4 |
| G3D-028 | All technologies | Common monetary year and inflation conversion | Low for dispatch / critical for CAPEX comparison | ECB/HICP + technology-data metadata | P1 | Phase 4 |
| G3D-029 | All technologies | Technology-data release pinned to CLI project | Critical | Actual PyPSA-Eur repository/lock file | P0 | Before model build |
| G3D-030 | All dispatchable units | Plant/cohort assignment | Critical | Terna Gaudì / ENTSO-E / EIA-like plant data | P0 | Phase 4 |

## 3. Gerarchia delle fonti

1. versione di `technology-data` effettivamente pinning nel progetto PyPSA-Eur;
2. JRC e Commissione europea;
3. Danish Energy Agency;
4. ENTSO-E e dati TSO;
5. ISPRA, ARERA e GME;
6. altre fonti tecniche primarie.

## 4. Gap che bloccano il model build

I principali P0 sono:

- efficienze e coorti del parco termoelettrico;
- availability degli impianti;
- profili orari FER;
- efficienze BESS e pompaggi;
- inflow e capacità energetica idroelettrica;
- parametri operativi nucleare e CCUS;
- prezzi gas/CO₂ 2050;
- VOLL;
- release `technology-data` pinning;
- mapping impianto–coorte.

## 5. Gap contestuali

CAPEX, FOM, vita e WACC non bloccano il dispatch-only. Diventano necessari per:

- confronto economico;
- revenue adequacy;
- eventuale capacity expansion;
- interpretazione degli scenari.

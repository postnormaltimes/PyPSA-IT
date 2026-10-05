# BLK-007 — Availability e refuelling nucleare

## Stato

**CLOSED**

## Correzione metodologica fondamentale

La Francia 2022 non è utilizzata per calibrare il caso centrale né la sensitivity bassa ordinaria. È conservata esclusivamente come stress test comune e correlato (`EDF_2022_SCC_STRESS`), perché la flotta fu colpita simultaneamente da stress-corrosion cracking, ritardi manutentivi e fermate ispettive straordinarie.

La baseline è invece calibrata sulla flotta nucleare statunitense EIA/NRC, che offre una serie pluriennale stabile e dati operativi sulle fermate per refuelling.

## Parametri adottati

| Parametro | Caso centrale | Base empirica |
| --- | ---: | --- |
| Availability annuale | 92,45% | media e mediana EIA USA 2015–2024 |
| Ciclo combustibile | 24 mesi | intervallo EIA 18–24 mesi; configurazione NRC a 24 mesi |
| Durata fermata di refuelling | 33 giorni | punto centrale delle osservazioni EIA 25, 32, 38 e 35 giorni |
| Unità equivalenti | 8 × 1 GW | disaggregazione modellistica degli 8 GW PNIEC |
| Outage programmati nel 2050 | 4 × 1 GW | metà delle unità in ciascun anno del ciclo biennale |
| Fattore residuo fuori refuelling | 0,968271 | calibrazione alla availability annuale del 92,45% |
| Massimo outage programmato simultaneo | 1 GW | calendario scaglionato |

## Struttura della flotta

- Porto Tolle: quattro unità equivalenti da 1 GW;
- Latina: tre unità equivalenti da 1 GW;
- Trino: una unità equivalente da 1 GW.

Il modello conserva tre generatori aggregati per sito. La fermata di una unità equivalente riduce il `p_max_pu` del sito della quota corrispondente:

- Porto Tolle: decremento di 0,25 prima del fattore residuo;
- Latina: decremento di 1/3;
- Trino: decremento di 1,00.

## Calendario centrale 2050

| Unità | Sito | Inizio | Fine esclusiva | Durata |
| --- | --- | --- | --- | ---: |
| PT-1 | Porto Tolle | 10 febbraio | 15 marzo | 33 giorni |
| LAT-1 | Latina | 1 aprile | 4 maggio | 33 giorni |
| PT-2 | Porto Tolle | 15 settembre | 18 ottobre | 33 giorni |
| TRI-1 | Trino | 25 ottobre | 27 novembre | 33 giorni |

Le quattro unità restanti sono assegnate al secondo anno del ciclo, così che ogni unità sia fermata una volta ogni 24 mesi.

## Calibrazione del profilo

La disponibilità pianificata della flotta nel representative year è:

```text
1 − (4 outage × 33 giorni) / (8 unità × 365 giorni)
= 95,479452%
```

Il fattore residuo fuori refuelling è calibrato per ottenere l'availability annuale EIA:

```text
0,9245 / 0,95479452 = 0,96827116
```

Questo fattore rappresenta in forma deterministica le perdite attese non associate al refuelling, incluse le indisponibilità non programmate ordinarie. Non viene quindi applicato un secondo forced-outage multiplier.

## Riconciliazione con il PNIEC

Il target PNIEC di 64,2 TWh per 8 GW equivale a:

```text
64,2 / (8 × 8,760) = 91,6096%
```

Il profilo centrale rende disponibili 64,78896 TWh, con 0,58896 TWh di margine rispetto al target PNIEC. Il modello non forza la produzione a 64,2 TWh: il target resta un controllo annuale, mentre il dispacciamento può produrre meno per modulazione economica, congestioni o curtailment.

## Sensitivity

| Caso | Availability | Ciclo | Durata outage | Uso |
| --- | ---: | ---: | ---: | --- |
| `NUC_HIGH` | 93,4% | 24 mesi | 25 giorni | high-performance case |
| `NUC_CENTRAL` | 92,45% | 24 mesi | 33 giorni | base |
| `NUC_LOW_NORMAL` | 90,8% | 18 mesi | 38 giorni | low ordinario |
| `EDF_2022_SCC_STRESS` | non assimilabile a normale availability | n.a. | shock correlato | extreme stress only |

## Ruolo dell'evidenza EDF

EDF 2024–2025 è utilizzata per:

- coordinamento delle fermate a livello di flotta;
- verifica che la produzione effettiva sia distinta dall'availability;
- rappresentazione della modulazione in un sistema ad alta quota nucleare e rinnovabile.

Nel 2025 EDF ha comunicato 373 TWh di produzione, buona disponibilità, fermate programmate ben gestite e 33 TWh di modulazione. Il dato conferma che `p_max_pu` è un tetto tecnico e non un obbligo di generazione.

## File model-ready

`nuclear_pmax_2050_central.csv` contiene 8.760 righe e le colonne:

- `timestamp`;
- `porto_tolle_p_max_pu`;
- `latina_p_max_pu`;
- `trino_p_max_pu`;
- `fleet_p_max_pu`;
- `planned_outage_unit`.

Controlli sul CSV:

- prima ora: 2050-01-01 00:00;
- ultima ora: 2050-12-31 23:00;
- media flotta: 0,924500000;
- minimo flotta: 0,847237267;
- massimo flotta: 0,968271162;
- 792 ore per ciascuna delle quattro fermate;
- nessuna sovrapposizione fra outage programmati.

## Integrazione PyPSA

Le tre colonne site-specific devono alimentare `Generator.p_max_pu` dei generatori definiti da BLK-006:

```text
NUC_PORTO_TOLLE -> porto_tolle_p_max_pu
NUC_LATINA       -> latina_p_max_pu
NUC_TRINO        -> trino_p_max_pu
```

Il file è una serie deterministica centrale. Una futura sensitivity stocastica può utilizzare i dati giornalieri NRC, senza modificare la baseline.

## QA di chiusura

- media EIA 2015–2024: PASS;
- mediana EIA 2015–2024: PASS;
- durata centrale 33 giorni: PASS;
- profilo di 8.760 ore: PASS;
- availability annuale 92,45%: PASS;
- outage simultaneo massimo 1 GW: PASS;
- margine sul target PNIEC: PASS;
- Francia 2022 esclusa dalla baseline: PASS;
- nessun errore formula: PASS;
- nessun `REVIEW`: PASS.

## Blocker rimanente

- BLK-008 — prezzo CO₂ 2050.

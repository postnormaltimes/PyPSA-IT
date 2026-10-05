# BLK-005 — Risoluzione di availability, manutenzioni e indisponibilità termoelettriche

## Stato

**BLK-005: CLOSED**

## Principio metodologico

L'indisponibilità termoelettrica è rappresentata in tre livelli distinti:

1. **capacità strutturale (`p_nom`)**;
2. **manutenzione programmata (`p_max_pu = 0` durante le fermate)**;
3. **derating accidentale, ambientale e operativo fuori manutenzione**.

Questa separazione evita di applicare più volte la stessa indisponibilità.

## 1. Capacità strutturale

I Rapporti di Adeguatezza Terna indicano:

| Anno dati | Installato | Massimo disponibile | Disponibilità strutturale |
| ---: | ---: | ---: | ---: |
| 2021 | 60,6 GW | 54,8 GW | 90,43% |
| 2022 | 61,2 GW | 55,6 GW | 90,85% |
| 2023 | 62,1 GW | 57,6 GW | 92,75% |
| 2024 | 61,1 GW | 57,7 GW | 94,44% |
| **Media** |  |  | **92,12%** |

La media storica è conservata come QA e fallback.

Nel modello principale, gli impianti soggetti a indisponibilità di lunga durata, vincoli autorizzativi o dismissione sono esclusi o ridotti tramite il plant-status workflow di PyPSA-Eur. Il 92,12% non deve quindi essere moltiplicato nuovamente quando tale filtro è attivo.

## 2. Manutenzione programmata

Gli standard Terna Capacity Market per il 2027, derivati dai dati storici 2021–2023, sono:

| Tecnologia | Ore equivalenti annue | Giorni equivalenti | Quota annua | Disponibilità al netto della sola manutenzione |
| --- | ---: | ---: | ---: | ---: |
| CCGT / combinato | 1.353 h | 56 | 15,45% | 84,55% |
| OCGT / turbogas | 857 h | 36 | 9,78% | 90,22% |
| Termico tradizionale | 1.344 h | 56 | 15,34% | 84,66% |

### Implementazione preferita

Per unità o coorti:

```text
p_max_pu = 0 durante il periodo di manutenzione programmata
```

Le fermate dovrebbero essere distribuite nei periodi di minore domanda residua e non rese contemporanee per l'intera flotta.

### Fallback aggregato

Quando non è disponibile un calendario unit-level:

```text
availability_maintenance = 1 - ore_manutenzione / 8.760
```

## 3. Derating operativo, ambientale e accidentale

Per gli impianti termoelettrici esistenti e rilevanti si adottano i fattori medi attesi Terna per area:

| Zona modello | Derating | `p_max_pu` fuori manutenzione |
| --- | ---: | ---: |
| NORD | 22% | 0,78 |
| CNOR | 31% | 0,69 |
| CSUD | 24% | 0,76 |
| SUD | 23% | 0,77 |
| CALA | 17% | 0,83 |
| SICI | 25% | 0,75 |
| SARD | 26% | 0,74 |

Il valore SARD è esatto perché SARD_NORD e SARD_SUD hanno entrambe derating del 26%.

Per i nuovi impianti:

| Asset | Derating |
| --- | ---: |
| Nuovo termoelettrico, raffreddamento ad aria o non specificato | 10% |
| Nuovo CCGT esclusivamente raffreddato ad acqua | 20% |

## Formula modellistica

Durante le ore non interessate da manutenzione:

```text
p_max_pu = 1 - derating_zonale_o_tecnologico
```

Durante la manutenzione:

```text
p_max_pu = 0
```

Per una sensitivity aggregata annuale:

```text
availability_annua_equivalente
= (1 - ore_manutenzione / 8.760)
  × (1 - derating)
```

## Forced outage

Non viene aggiunto un ulteriore tasso uniforme di forced outage.

Il fattore Terna Capacity Market è adottato come proxy ufficiale comprensivo delle componenti accidentali, ambientali e degli altri vincoli tecnico-operativi fuori manutenzione. Un secondo moltiplicatore produrrebbe doppio conteggio.

Il Rapporto di Adeguatezza Terna 2025 conferma che nella modellazione probabilistica Terna impiega profili orari di guasto per ciascun generatore e una schedulazione annuale delle manutenzioni. Il workbook conserva pertanto la possibilità di sostituire il proxy zonale con profili plant-level quando disponibili nel workflow PyPSA-Eur.

## Sensitivity

Il workbook consente tre varianti:

1. **Central** — manutenzione Terna + derating zonale;
2. **New-build / high availability** — manutenzione tecnologica + derating 10%;
3. **Water-cooling stress** — CCGT esplicitamente raffreddato ad acqua con derating 20%.

La media strutturale 92,12% è esclusivamente una verifica di ordine di grandezza.

## Integrazione PyPSA

Il mapping `MAP-037` prescrive:

- filtraggio dello `p_nom` tramite stato impianto;
- manutenzione programmata come serie `Generator.p_max_pu`;
- derating zonale fuori manutenzione;
- fallback statico soltanto quando non è disponibile un profilo unit-level;
- nessun doppio conteggio con i default o i dati impianto del progetto PyPSA-Eur.

## QA di chiusura

- sette zone coperte — PASS;
- tutti i derating compresi tra zero e uno — PASS;
- CCGT 1.353 h — PASS;
- OCGT 857 h — PASS;
- termico tradizionale 1.344 h — PASS;
- nuovo termico 10% — PASS;
- CCGT ad acqua 20% — PASS;
- media strutturale 92,12% — PASS;
- nessun errore formula — PASS;
- nessun risultato REVIEW.

## Blocker rimanenti

- BLK-007 — availability e refuelling nucleare;
- BLK-008 — prezzo CO₂ 2050.

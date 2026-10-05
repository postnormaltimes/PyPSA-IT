# Fase 5 — Guida al workbook model-ready

## Finalità

Il workbook consolida la catena di evidenza Terna–PNIEC in una struttura leggibile, verificabile e predisposta per l’esportazione verso PyPSA/PyPSA-Eur in modalità solo dispacciamento.

## Scenari principali

- 2040: `IT2040_BASE_DEIT`
- 2050: `IT2050_BASE_PNIEC_NUCLEAR_8GW`

Sensitività principali:

- `IT2040_ALT_GAIT`
- `IT2040_ALT_SLOW`
- `IT2040_GRID_DELAY`
- `IT2050_SENS_NO_NUCLEAR`

## Regola operativa

Il workbook è **pronto per la costruzione degli input**, ma non autorizza ancora il run.

Le categorie sono:

- `APPROVED`: input adottato;
- `PROVISIONAL`: bridge da riesaminare prima del run;
- `BLOCKING`: dato P0 mancante;
- `QA ONLY`: controllo, non input orario;
- `DO NOT SUM`: sottoinsieme informativo escluso dai totali.

## Input 2050 provvisori

Il PNIEC non quantifica lo storage del ramo nucleare. Il workbook adotta per la Fase 5:

- 237,667 GWh;
- 43,653 GW;
- durata aggregata 5,444 h.

Il valore deriva dall’estrapolazione della relazione Terna fra capacità FRNP e accumuli ed è marcato come provvisorio.

## QA

- 24 controlli formula-driven sul workbook finale;
- 59 controlli di riconciliazione persistiti dalla Fase 4;
- 83 risultati PASS;
- 0 REVIEW;
- nessun errore formula rilevato.

## Blocchi pre-run

Restano aperti:

1. domanda oraria zonale;
2. profili FER;
3. idroelettrico;
4. coorti termoelettriche;
5. availability e outage;
6. siting nucleare;
7. refuelling nucleare;
8. prezzo CO₂ 2050;
9. VOLL;
10. pin PyPSA-Eur/technology-data;
11. sistemi esteri;
12. profili P2X e nuovi carichi.

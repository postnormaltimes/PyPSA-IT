# 15 — Assunzioni e riconciliazioni — v1.2

## Decisioni approvate

- `IT2040_BASE_DEIT` come base 2040;
- `IT2050_BASE_PNIEC_NUCLEAR_8GW` come base 2050;
- scenario Terna senza nucleare come sensitivity;
- small-scale BESS: due terzi a 2h e un terzo a 4h;
- Capacity Market: durata 4h;
- MACSE: 42 GWh, incluso nella capacità utility-scale e separato dal Capacity Market;
- EUR2025;
- `technology-data v0.15.0` come candidato subordinato al pin CLI;
- rete PDS 2040 come floor del 2050.

## Assunzione eliminata

La precedente interpolazione personalizzata a 4 GW è classificata **SUPERSEDED** e non compare più negli scenari attivi.

## Bridge storage PNIEC

| Voce | Valore | Classificazione |
| --- | ---: | --- |
| Energia BESS | 237.666667 GWh | provvisoria |
| Potenza BESS | 43.653061 GW | provvisoria |
| Durata aggregata | 5.444444 h | derivata dal caso Terna con nucleare |

Il bridge serve a completare lo schema di input della Fase 5, non viene presentato come valore diretto PNIEC.

## Boundary contabili

- MACSE è contenuto nella categoria Utility Scale;
- Capacity Market e MACSE rimangono distinti;
- pompaggi restano separati dalle batterie;
- pipeline di connessione esclusa dalla capacità certa;
- LCOE, CAPEX e FOM restano fuori dall’obiettivo dispatch-only;
- importazioni annuali PNIEC sono QA e non un vincolo orario esogeno se i sistemi esteri sono modellati endogenamente.

## Riconciliazione

La Fase 4 v1.2 contiene **63 controlli PASS e 0 REVIEW**.

Sono verificati:

- domanda rigida + P2X;
- capacità zonali e nazionali;
- nucleare pari a 8 GW;
- produzione nucleare pari a 64,2 TWh;
- capacità e durata BESS;
- produzione nazionale;
- import netto;
- perdite storage implicite;
- rete;
- MACSE;
- tassonomia;
- conversioni monetarie.

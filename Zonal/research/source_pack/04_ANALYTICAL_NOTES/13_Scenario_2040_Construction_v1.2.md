# 13 — Costruzione dello scenario 2040 — v1.2

## Scenario base approvato

Lo scenario centrale 2040 resta **`IT2040_BASE_DEIT`**.

| Dimensione | Impostazione |
| --- | --- |
| Domanda totale | 439 TWh |
| Solare | 120,9 GW |
| Eolico onshore | 34,1 GW |
| Eolico offshore | 15,2 GW |
| Rete | Piano di Sviluppo 2025, orizzonte 2040 |
| Capacità | Fisse, non estendibili |
| Dispatch | Lineare, senza unit commitment |
| Base monetaria | EUR2025 |
| Technology-data | v0.15.0 provvisoria; prevale il pin CLI |

## Accumuli

### Small-scale

È confermata la ripartizione modellistica:

- 66,7% dell’energia nella classe da 2 ore;
- 33,3% nella classe da 4 ore.

### Capacity Market

La classe Capacity Market rimane distinta dal MACSE:

- energia: valori DDS;
- durata: 4 ore;
- potenza: energia divisa per quattro.

### MACSE

Il percorso approvato è pari a 42 GWh:

| Tranche | Consegna | Energia | Status |
| --- | ---: | ---: | --- |
| Prima asta | 2028 | 10 GWh | assegnata |
| Seconda asta | 2029 | 16 GWh | fabbisogno approvato |
| Asta successiva | 2030, working assumption | 16 GWh | rivedibile |
| **Totale** |  | **42 GWh** | adottato nello scenario |

Il MACSE è trattato come sottoinsieme della capacità utility-scale già presente nello scenario:

```text
Utility Scale totale
= MACSE
+ Utility Scale residuale merchant/private
```

Non viene quindi aggiunto una seconda volta al totale BESS. Con la durata utility-scale di otto ore:

```text
42 GWh / 8 h = 5,25 GW
```

## Sensitività 2040

- `IT2040_ALT_GAIT`;
- `IT2040_ALT_SLOW`;
- `IT2040_GRID_DELAY`.

## Stato

Lo scenario annuale, zonale e di rete è approvato per la costruzione del workbook della Fase 5. Il run rimane subordinato ai profili orari, all’idroelettrico, alle coorti termoelettriche e agli altri input operativi.

# BLK-006 — Risoluzione del siting nucleare PNIEC 2050

## Decisione adottata

Lo scenario `IT2050_BASE_PNIEC_NUCLEAR_8GW` è rappresentato con tre localizzazioni:

| Sito | Capacità | Regione | Zona Terna | Bus zonale |
| --- | ---: | --- | --- | --- |
| Porto Tolle | 4 GW | Veneto | NORD | `IT_NORD` |
| Latina | 3 GW | Lazio | CENTRO SUD | `IT_CSUD` |
| Trino | 1 GW | Piemonte | NORD | `IT_NORD` |
| **Totale** | **8 GW** |  |  |  |

Riepilogo zonale:

- NORD: 5 GW;
- CENTRO SUD: 3 GW;
- Italia: 8 GW.

## Natura dell'assunzione

La capacità assegnata ai tre siti è una decisione modellistica dell'utente.

Le fonti ufficiali sono utilizzate per validare:

- localizzazione geografica;
- regione;
- zona di mercato Terna;
- presenza di infrastrutture RTN nell'area.

Non dimostrano:

- autorizzazione di nuovi impianti;
- idoneità nucleare dei siti;
- capacità di connessione disponibile;
- schema definitivo di connessione;
- fattibilità ambientale o autorizzativa.

Porto Tolle è un sito energetico brownfield, non un ex sito nucleare.

## Evidenza geografica e di rete

### Zonizzazione

L'Allegato A.24 di Terna assegna:

- Piemonte e Veneto alla zona NORD;
- Lazio alla zona CENTRO SUD.

### Porto Tolle

Il Piano di Sviluppo Terna 2025 documenta la stazione RTN 380/132 kV di Porto Tolle e il completamento di un nuovo stallo 132 kV nel 2023–2024.

La coordinata inserita nel workbook è un centro-sito pubblico approssimativo:

- 44.956483 N;
- 12.487795 E.

### Latina

La documentazione della Regione Lazio individua l'area Sogin di Borgo Sabotino e fornisce il centroide UTM dell'area. La conversione in WGS84 utilizzata nel workbook è:

- 41.418493 N;
- 12.809786 E.

La documentazione Terna identifica inoltre la stazione 380 kV di Latina e la rete locale associata all'area nucleare.

### Trino

La dichiarazione ambientale Sogin fornisce le coordinate UTM-WGS84 del sito. La conversione utilizzata nel workbook è:

- 45.184093 N;
- 8.277556 E.

Il Piano di Sviluppo Terna documenta la stazione 380 kV di Trino e la direttrice Trino–Lacchiarella.

## Implementazione nel modello

Nel modello zonale:

```text
NUC_PORTO_TOLLE -> IT_NORD -> 4,000 MW
NUC_TRINO        -> IT_NORD -> 1,000 MW
NUC_LATINA       -> IT_CSUD -> 3,000 MW
```

La precedente riga nazionale da 8 GW è mantenuta come controllo QA e marcata:

`QA AGGREGATE / DO NOT EXPORT`

Le tre righe site-specific sono gli input da esportare verso `Generator.bus`, `Generator.carrier` e `Generator.p_nom`.

## Regola per una futura versione nodale

Qualora il modello passi da zone di mercato a bus AC:

1. associare ciascun sito al bus AC più vicino;
2. mantenere il bus nella medesima zona Terna;
3. conservare 5.000 MW complessivi in NORD;
4. conservare 3.000 MW complessivi in CENTRO SUD;
5. non interpretare la vicinanza geografica come capacità di connessione verificata.

## QA di chiusura

- capacità nazionale: 8.000 MW — PASS;
- NORD: 5.000 MW — PASS;
- CENTRO SUD: 3.000 MW — PASS;
- tre righe esportabili — PASS;
- riga nazionale esclusa dall'export — PASS;
- nessun doppio conteggio — PASS.

## Stato

**BLK-006: CLOSED**

Restano aperti:

- BLK-005 — availability e forced outages termoelettrici;
- BLK-007 — availability e refuelling nucleare;
- BLK-008 — prezzo CO₂ 2050.

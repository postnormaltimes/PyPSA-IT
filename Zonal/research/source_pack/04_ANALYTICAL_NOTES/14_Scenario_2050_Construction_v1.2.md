# 14 — Costruzione dello scenario 2050 — v1.2

## Scenario base approvato

Il caso centrale è **`IT2050_BASE_PNIEC_NUCLEAR_8GW`**.

La precedente interpolazione personalizzata a 4 GW è stata eliminata. Il nuovo scenario utilizza direttamente il ramo PNIEC 2050 con nucleare.

## Parametri PNIEC diretti

| Dimensione | Valore |
| --- | ---: |
| Domanda totale | 583,1 TWh |
| Domanda rigida e perdite | 501,6 TWh |
| Power-to-X | 81,5 TWh |
| Fotovoltaico | 245 GW |
| Eolico onshore | 36 GW |
| Eolico offshore | 15 GW |
| Nucleare | 8 GW |
| Produzione nucleare | 64,2 TWh |
| Load factor nucleare implicito | 91.610% |
| Import netto — QA | 17,7 TWh |
| Produzione nazionale — QA | 596,8 TWh |

Gli 8 GW costituiscono il valore medio PNIEC; la fonte riporta un intervallo di 7,5–8,5 GW.

## Disaggregazione zonale

Il PNIEC fornisce capacità nazionali. Per il workbook model-ready:

- solare distribuito e utility sono mantenuti separati;
- solare, onshore e offshore sono distribuiti usando le rispettive quote zonali DE-IT 2040;
- il nucleare resta nazionale con **siting aperto**, senza assegnazione arbitraria a una zona.

## Accumuli: bridge provvisorio

Il PNIEC non quantifica direttamente gli accumuli del caso 2050. Per rendere completa la struttura della Fase 5 è adottato un bridge trasparente:

```text
Terna: 246 GW FRNP → 196 GWh
Terna: 270 GW FRNP → 216 GWh
PNIEC: 296 GW FRNP → 237.667 GWh
```

La potenza mantiene la durata aggregata del caso tecnico Terna con nucleare:

```text
durata = 196 GWh / 36 GW = 5.444 h
potenza = 237.667 GWh / 5.444 h
         = 43.653 GW
```

Questo valore è classificato **PROVISIONAL FOR PHASE 5** e dovrà essere sostituito se viene acquisita una fonte diretta compatibile con il PNIEC prima del primo run 2050.

## Bilancio annuale di controllo

Sono conservati come QA:

- produzione nazionale: 596,8 TWh;
- fotovoltaico: 345,8 TWh;
- altre FER: 164,1 TWh;
- nucleare: 64,2 TWh;
- import netto: 17,7 TWh;
- perdite degli accumuli implicite: 31.4 TWh.

## Sensitività

- `IT2050_SENS_NO_NUCLEAR`: scenario tecnico Terna senza nucleare;
- `TERNA2050_NUCLEAR_10GW_BENCHMARK`: endpoint tecnico Terna con 10 GW.

## Stato

La specifica è pronta per la Fase 5. Il run resta bloccato da:

- siting nucleare;
- availability e refuelling;
- profili orari;
- idroelettrico;
- prezzo CO₂ 2050;
- VOLL;
- pin effettivo PyPSA-Eur/technology-data.

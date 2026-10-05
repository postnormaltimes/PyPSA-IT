# 07 — Evidenza sulla capacità di generazione

## 1. Capacità nazionale 2040

| Tecnologia | DE-IT 2040 | GA-IT 2040 | PNIEC Slow 2040 | Unità |
| --- | --- | --- | --- | --- |
| Totale FER installata | 192,0 | 176,0 | 166,0 | GW |
| Solare | 121,0 | 111,0 | 105,1 | GW |
| Eolico onshore | 34,0 | 31,0 | 29,0 | GW |
| Eolico offshore | 15,1 | 12,1 | 10,0 | GW |
| Parco termoelettrico aggregato | 55,0 | 55,0 | 55,0 | GW |
| Idrico e pompaggi | 23,0 | 23,0 | 23,0 | GW |

La capacità termoelettrica è aggregata e dovrà essere disaggregata in coorti tecnologiche; i **23 GW idrici e pompaggi** sono un'inferenza di continuità dal quadro 2050 e restano marcati `D — Inferred`, non input definitivo.

## 2. Distribuzione zonale 2040

| Zona | DE solare | DE eolico | GA solare | GA eolico | Slow solare | Slow eolico |
| --- | --- | --- | --- | --- | --- | --- |
| Nord | 42,4 | 2,1 | 40,5 | 1,7 | 36,5 | 1,6 |
| Centro Nord | 9,6 | 1,2 | 9,3 | 0,9 | 8,9 | 0,8 |
| Centro Sud | 20,1 | 8,4 | 18,4 | 7,4 | 17,6 | 6,7 |
| Sud | 21,0 | 17,9 | 17,7 | 15,8 | 17,6 | 13,5 |
| Calabria | 3,6 | 3,9 | 3,4 | 3,4 | 3,3 | 3,4 |
| Sicilia | 14,8 | 9,3 | 13,4 | 8,2 | 13,3 | 8,0 |
| Sardegna | 9,4 | 6,5 | 8,4 | 5,6 | 8,0 | 5,1 |

Unità: GW. Il workbook conserva separatamente solare distribuito/utility ed eolico onshore/offshore in 252 righe.

## 3. Capacità nazionale 2050

| Scenario | Totale FRNP | Solare | Eolico onshore | Eolico offshore | Nucleare | Unità |
| --- | --- | --- | --- | --- | --- | --- |
| PNIEC 2050 con nucleare | 296,0 | 245,0 | 36,0 | 15,0 | 8,0 | GW |
| PNIEC 2050 senza nucleare | 296,0 | 245,0 | 36,0 | 15,0 | 0,0 | GW |
| Terna 2050 con nucleare | 246,0 | 180,0 | 46,0 | 20,0 | 10,0 | GW |
| Terna 2050 senza nucleare | 270,0 | 200,0 | 50,0 | 20,0 | 0,0 | GW |

- PNIEC: 245 GW solare, 36 GW onshore, 15 GW offshore.
- Terna con nucleare: 180 GW solare, 46 GW onshore, 20 GW offshore, 10 GW nucleare.
- Terna senza nucleare: 200 GW solare, 50 GW onshore, 20 GW offshore.
- Figura 54 Terna con nucleare: circa 23 GW idrico+pompaggi, 36 GW potenza BESS, 330 GW installato complessivo.
- Energia BESS Terna: 196 GWh con nucleare e 216 GWh senza; estrazione completa in Fase 3B.

## 4. Uso modellistico

| Classe | Trattamento |
|---|---|
| Solare/eolico DDS 2040 | Input diretto nazionale e zonale dopo mapping |
| Totale FER | Controllo, non componente autonomo |
| Termoelettrico DDS | Controllo aggregato, non ancora input tecnologico |
| Idrico/pompaggi | Fase 3B |
| Capacità Terna 2050 | Candidato nazionale; zonalizzazione da costruire |
| Capacità PNIEC 2050 | Benchmark policy |
| Pipeline connessioni | Esclusa finché non filtrata per maturità |

## 5. QA

Eseguiti **38 controlli**: **34 PASS** e **4 REVIEW**. Le review riguardano scarti di 0,2 GW da arrotondamento zonale e uno scarto di 2 TWh nel bilancio PNIEC Slow 2030. I dati di fonte non sono stati corretti arbitrariamente.

## 6. Gap

- termoelettrico per combustibile, tecnologia, efficienza ed età;
- idroelettrico tra run-of-river, serbatoio e pompaggio;
- storage MW/MWh, durata, efficienze e zona;
- localizzazione zonale 2050;
- status di nuovi impianti e dismissioni;
- availability, outage e derating;
- siting nucleare e capacità CCS/programmabile low-carbon.

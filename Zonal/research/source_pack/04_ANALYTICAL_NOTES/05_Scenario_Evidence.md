# 05 — Evidenza sugli scenari

## 1. Stato e perimetro

La Fase 3A ha incorporato il **Documento di Descrizione degli Scenari 2024 (DDS 2024)** come nuova fonte primaria `S09`. Il DDS è il collegamento tecnico tra PNIEC 2024, scenari europei TYNDP e Piano di Sviluppo Terna: definisce storyline, input di scenario, domanda, capacità FER, bilanci elettrici e risultati di simulazione fino al 2040.

I dati sono stati classificati separatamente come osservati storici, target di policy, assunzioni di scenario, configurazioni tecniche, risultati di simulazione e valori derivati o contestuali.

## 2. Crosswalk degli scenari

| Scenario ID | Etichetta | Anno | Famiglia | Tipo | Status policy | Ruolo elettrico | Uso previsto | Fonte | Pagine PDF |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| HIST_2023 | Storico 2023 | 2023 | Historical | Dato osservato storico | Observed | Baseline e QA/backcast. | Calibrazione; non scenario futuro. | S09 | 14; 48; 59-67 |
| PNIEC_POLICY_2030 | PNIEC Policy 2030 | 2030 | DDS 2024 | Scenario tecnico coerente con policy | Raggiunge target 2030 | Forte elettrificazione, coal phase-out, FER e storage. | Ponte di traiettoria e controllo per 2040. | S09 | 40; 48-67 |
| PNIEC_SLOW_2030 | PNIEC Slow 2030 | 2030 | DDS 2024 | Scenario contrastante | Transizione ritardata | Domanda, FER, storage ed elettrolizzatori inferiori. | Sensitivity downside / slower transition. | S09 | 41; 48-67 |
| DE_IT_2035 | DE-IT 2035 | 2035 | DDS 2024 | Scenario tecnico di policy | Percorso net-zero | Massimizza elettrificazione e sviluppo FER-E. | Traiettoria verso il caso 2040 DE-IT. | S09 | 40-41; 48-67 |
| GA_IT_2035 | GA-IT 2035 | 2035 | DDS 2024 | Scenario tecnico di policy | Percorso net-zero | Elettrificazione inferiore a DE-IT; più vettori molecolari. | Scenario alternativo di traiettoria. | S09 | 40-41; 48-67 |
| PNIEC_SLOW_2035 | PNIEC Slow 2035 | 2035 | DDS 2024 | Scenario contrastante | Transizione ritardata | Domanda e capacità inferiori. | Sensitivity downside. | S09 | 41; 48-67 |
| DE_IT_2040 | DE-IT 2040 | 2040 | DDS 2024 | Scenario tecnico di policy | Percorso net-zero 2050 | Domanda e FER più alte; 76% FER sul fabbisogno. | Candidato raccomandato al caso base 2040. | S09 | 14; 41; 48; 59-67 |
| GA_IT_2040 | GA-IT 2040 | 2040 | DDS 2024 | Scenario tecnico di policy | Percorso net-zero 2050 | Domanda e capacità inferiori a DE-IT; maggiore domanda H2 finale. | Alternativa 2040 per confronto. | S09 | 14; 41; 48; 59-67 |
| PNIEC_SLOW_2040 | PNIEC Slow 2040 | 2040 | DDS 2024 | Scenario contrastante | Transizione ritardata | Valori minimi di domanda, FER, storage ed elettrolizzatori. | Sensitivity conservative/downside 2040. | S09 | 14; 41; 48; 59-67 |
| PNIEC_2050_NUCLEAR | PNIEC 2050 con nucleare | 2050 | PNIEC 2024 exploratory | Risultato di modello di policy esplorativo | Net zero esplorativo | Domanda più elevata, minore fabbisogno di CCS. | Benchmark di policy, non input tecnico finale automatico. | S08/S07 | S08 90-93; S07 52 |
| PNIEC_2050_NO_NUCLEAR | PNIEC 2050 senza nucleare | 2050 | PNIEC 2024 exploratory | Risultato di modello di policy esplorativo | Net zero esplorativo | Domanda minore e produzione CCS maggiore. | Benchmark di policy alternativo. | S08/S07 | S08 90-93; S07 52 |
| TERNA_2050_NUCLEAR | Terna 2050 con nucleare | 2050 | Terna 2050 | Scenario tecnico esplorativo | Analisi di lungo termine | Fabbisogno 583 TWh; 246 GW FRNP; 196 GWh storage. | Candidato principale 2050 con nucleare. | S07 | 52-64 |
| TERNA_2050_NO_NUCLEAR | Terna 2050 senza nucleare | 2050 | Terna 2050 | Scenario tecnico esplorativo | Analisi di lungo termine | 270 GW FRNP; 216 GWh storage. | Candidato principale 2050 senza nucleare. | S07 | 52-64 |

## 3. Configurazione 2040

- **DE-IT 2040:** forte elettrificazione, sviluppo elevato di FER e accumuli; candidato naturale al caso centrale.
- **GA-IT 2040:** maggiore ruolo di idrogeno, gas verdi, importazioni energetiche e CCS; scenario strutturalmente alternativo.
- **PNIEC Slow 2040:** ritardo nell'attuazione delle misure; scenario conservativo/downside.

Il Piano di Sviluppo Terna seleziona principalmente PNIEC Policy 2030, DE-IT 2035/2040 e PNIEC Slow per le analisi di rete. GA-IT resta essenziale nel database come scenario alternativo DDS.

### Indicatori principali 2040

| Scenario | Fabbisogno totale | Usi finali | H₂/P2X | FER installata | Solare | Eolico onshore | Eolico offshore | Produzione FER | Produzione gas | Saldo estero |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| DE-IT 2040 | 439,0 | 377,8 | 27,5 | 192,0 | 121,0 | 34,0 | 15,1 | 336,0 | 59,0 | 47,0 |
| GA-IT 2040 | 415,2 | 355,0 | 27,5 | 176,0 | 111,0 | 31,0 | 12,1 | 309,0 | 61,0 | 46,0 |
| PNIEC Slow 2040 | 404,4 | 352,1 | 19,8 | 166,0 | 105,1 | 29,0 | 10,0 | 293,0 | 64,0 | 47,0 |

Unità: domanda e produzione in TWh; capacità in GW.

## 4. Configurazioni 2050

Sono mantenuti due livelli distinti:

1. **PNIEC 2050 esplorativo**, con e senza nucleare: domanda diversa nei due rami e mix FRNP comune di 296 GW.
2. **Terna 2050 tecnico**, con e senza nucleare: 583 TWh in entrambi i casi e mix rinnovabile con più eolico e meno fotovoltaico rispetto al benchmark PNIEC.

I rami non sono intercambiabili e non devono essere mediati.

## 5. Assunzioni commodity DDS

| Parametro | Anno | Valore | Unità | Base prezzi | Scenari | Cautela | Fonte |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Gas naturale | 2023 | 39,2 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Gas naturale | 2030 | 32,4 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Gas naturale | 2035 | 29,5 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Gas naturale | 2040 | 36,4 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Brent | 2023 | 45,0 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Brent | 2030 | 50,0 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Brent | 2035 | 55,4 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Brent | 2040 | 56,9 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Carbone | 2023 | 15,8 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Carbone | 2030 | 14,4 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Carbone | 2035 | 13,7 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| Carbone | 2040 | 13,7 | EUR/MWh | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| CO2 | 2023 | 92,0 | EUR/tCO2 | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| CO2 | 2030 | 95,0 | EUR/tCO2 | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| CO2 | 2035 | 100,0 | EUR/tCO2 | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |
| CO2 | 2040 | 100,0 | EUR/tCO2 | Real 2023 | All DDS 2024 scenarios | Not a forecast; parameter for scenario evaluation. | S09 p. PDF 28 |

Sono parametri di scenario, non previsioni puntuali; saranno ripresi nella Fase 3D per il `marginal_cost`.

## 6. Bilanci elettrici di controllo

| Voce | DE-IT 2040 | GA-IT 2040 | Slow 2040 | Terna 2050 nuc. | Terna 2050 no nuc. | Unità |
| --- | --- | --- | --- | --- | --- | --- |
| Fabbisogno elettrico totale | 439,0 | 415,0 | 404,0 | 583,0 | 583,0 | TWh |
| Produzione nazionale | — | — | — | 546,0 | 540,0 | TWh |
| Produzione FER | 336,0 | 309,0 | 293,0 | 435,0 | 474,0 | TWh |
| Solare | 168,0 | 151,0 | 144,0 | — | — | TWh |
| Eolico | 121,0 | 105,0 | 95,0 | — | — | TWh |
| Idroelettrico | 46,0 | 46,0 | 46,0 | — | — | TWh |
| Gas naturale | 59,0 | 61,0 | 64,0 | — | — | TWh |
| Altra produzione programmabile | 6,0 | 6,0 | 6,0 | 42,0 | 66,0 | TWh |
| Saldo import/export | 47,0 | 46,0 | 47,0 | 48,0 | 57,0 | TWh |
| Perdite accumuli | -9,0 | -7,0 | -6,0 | -11,0 | -14,0 | TWh |
| Overgeneration | -16,0 | -10,0 | -8,0 | 4,8 | 4,4 | TWh / % nel 2050 |

Nel 2050, l'overgeneration è riportata come percentuale del fabbisogno; nel DDS 2040 è riportata in TWh.

## 7. Orientamento provvisorio

- 2040: conservare DE-IT, GA-IT e PNIEC Slow; DE-IT è candidato centrale, non ancora baseline approvata.
- 2050: conservare entrambi i rami tecnici Terna; mantenere PNIEC come benchmark.
- Selezione finale solo dopo accumuli, idroelettrico, rete e costi.

## 8. Limiti

- localizzazione zonale completa disponibile per solare ed eolico DDS, non per tutte le tecnologie;
- 2050 prevalentemente nazionale;
- l'esercizio 2050 Terna non include un'ottimizzazione chiusa di tutti gli sviluppi di rete;
- capacità termoelettrica DDS aggregata;
- bilanci DDS arrotondati al TWh.

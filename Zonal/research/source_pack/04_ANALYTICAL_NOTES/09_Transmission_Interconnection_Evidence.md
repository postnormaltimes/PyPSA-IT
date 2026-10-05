# 09 — Evidenza su rete, capacità interzonale e interconnessioni

## 1. Esito e gerarchia delle fonti

La Fase 3C ha costruito un database separando rigorosamente:

1. limiti di transito correnti e stagionali;
2. capacità di scambio prospettiche per corridoio e direzione;
3. incrementi NTC attribuiti ai singoli progetti;
4. capacità nominale o fisica degli asset;
5. interconnessioni estere;
6. progetti intrazonali e di massimizzazione degli asset;
7. ipotesi di rete necessarie per il 2050.

La gerarchia adottata per la rete interna è:

- **S03, Figura 3:** fonte primaria più recente per i limiti direzionali 2024, 2030, 2035 e 2040;
- **S09, Figura 12:** benchmark DDS 2024, mantenuto per verificare le revisioni;
- **S03, Tabella 2:** effetti NTC dei progetti, non somma sostitutiva della Figura 3;
- **S05:** tecnologia, nominale, stato e cronologia dei progetti;
- **S01:** limiti correnti stagionali e topologia;
- **S07:** contesto 2050, ma senza una matrice NTC direzionale completa.

## 2. Topologia zonale proposta

Il modello mantiene sette bus geografici italiani:

`IT_NORD`, `IT_CNOR`, `IT_CSUD`, `IT_SUD`, `IT_CALA`, `IT_SICI`, `IT_SARD`.

Le sezioni correnti formano la catena continentale Nord–Centro Nord–Centro Sud–Sud–Calabria–Sicilia, con collegamenti alle isole e alle zone virtuali. Entro il 2040 compaiono inoltre sezioni dirette che non devono essere assorbite artificialmente nella sola catena esistente:

| Zona A | Zona B | Entrata topologica | Tipologia |
| --- | --- | --- | --- |
| NORD | SUD | By 2035 | New direct market section |
| NORD | CSUD | By 2035 | New direct market section |
| CSUD | SICI | By 2030 | New HVDC section |
| SARD | SICI | By 2030 | New HVDC section |
| SICI | TN | By 2030 / ELMED | New foreign interconnection |

In PyPSA-Eur ogni frontiera deve essere rappresentata con limiti direzionali. La soluzione raccomandata è usare due `Link` orientati, uno per ciascuna direzione, oppure un’unica formulazione equivalente solo quando la simmetria è dimostrata.

## 3. Limiti interzonali 2040 — PDS 2025

| Corridoio | A→B GW | B→A GW | Trattamento |
| --- | --- | --- | --- |
| NORD–CNOR | 6,1 | 5,3 | Direzionale |
| CNOR–CSUD | 5,4 | 6,1 | Direzionale |
| CSUD–SUD | 6,3 | 9,1 | Direzionale |
| SUD–CALA | 4,1 | 5,3 | Direzionale |
| CALA–SICI | 4,2 | 4,1 | Direzionale |
| CSUD–SARD | 1,7 | 1,9 | Direzionale |
| CNOR–CORS | 0,4 | 0,4 | Direzionale |
| CORS–SARD | 0,4 | 0,4 | Direzionale |
| NORD–SUD | 2,1 | 2,1 | Direzionale |
| NORD–CSUD | 2,1 | 2,1 | Direzionale |
| CSUD–SICI | 1,0 | 1,0 | Direzionale |
| SARD–SICI | 1,0 | 1,0 | Direzionale |

Questi valori costituiscono la proposta **2040 Base**. Sono limiti di scambio di mercato prospettici, non rating termici delle singole linee.

### Differenze rispetto al DDS 2024

| Direzione | DDS 2040 GW | PDS 2025 GW | Revisione GW |
| --- | --- | --- | --- |
| NORD→CNOR | 5,5 | 6,1 | 0,6 |
| CNOR→NORD | 4,7 | 5,3 | 0,6 |
| CNOR→CSUD | 5,2 | 5,4 | 0,2 |
| CSUD→CNOR | 5,9 | 6,1 | 0,2 |
| CSUD→SUD | 6,0 | 6,3 | 0,3 |
| SUD→CSUD | 8,8 | 9,1 | 0,3 |
| SUD→CALA | 4,2 | 4,1 | -0,1 |
| CALA→SUD | 5,5 | 5,3 | -0,1 |
| CALA→SICI | 4,1 | 4,2 | 0,1 |
| SICI→CALA | 4,0 | 4,1 | 0,1 |
| CSUD→SARD | 1,7 | 1,7 | 0,0 |
| SARD→CSUD | 1,9 | 1,9 | 0,0 |
| CNOR→CORS | 0,4 | 0,4 | 0,0 |
| CORS→CNOR | 0,4 | 0,4 | 0,0 |
| CORS→SARD | 0,4 | 0,4 | 0,0 |
| SARD→CORS | 0,5 | 0,4 | -0,1 |
| NORD→SUD | 2,0 | 2,1 | 0,1 |
| SUD→NORD | 2,0 | 2,1 | 0,1 |
| CSUD→SICI | 1,0 | 1,0 | 0,0 |
| SICI→CSUD | 1,0 | 1,0 | 0,0 |
| SARD→SICI | 1,0 | 1,0 | 0,0 |
| SICI→SARD | 1,0 | 1,0 | 0,0 |

Le differenze non sono errori. Il PDS 2025 incorpora aggiornamenti di progetto, massimizzazione degli asset e nuove analisi di rete. Per il caso base deve quindi prevalere il dato PDS 2025.

## 4. Limiti correnti 2024

S01 distingue:

- inverno: gennaio–aprile e ottobre–dicembre;
- estate: maggio–settembre.

Dove la fonte riporta due valori, il workbook conserva un intervallo `low/high`: il limite dipende dal fabbisogno residuo, dalle ore o da altre condizioni operative. Il valore massimo è usato soltanto per riconciliare la Figura 3 di S03; non viene trasformato in un profilo orario futuro.

Particolarmente rilevanti sono:

- NORD→CNOR: 3,4–4,3 GW;
- CNOR→NORD: 1,9–3,1 GW;
- CNOR→CSUD: 1,8–2,9 GW;
- SUD→CSUD: 5,1–5,2 GW;
- SARD→CSUD: 0,87–0,90 GW;
- il collegamento Corsica AC–Sardegna presenta limiti stagionali e direzionali distinti.

## 5. Progetti interzonali

Il workbook conserva una riga per ogni effetto direzionale di progetto. La regola di utilizzo è:

> il totale di corridoio della Figura 3 governa il `p_nom`; gli incrementi di progetto servono per audit, cronologia e sensitività, non devono essere sommati una seconda volta.

Esempi che dimostrano perché nominale e NTC non sono equivalenti:

- **Adriatic Link 436-P:** nominale 1.000 MW; effetti diversi su NORD–CNOR e CNOR–CSUD;
- **SA.CO.I.3 301-P:** nominale 400 MW; incremento NTC riportato di 100 MW;
- **Bolano–Annunziata 555-P:** capacità complessiva Sicilia–Calabria fino a 2.000 MW; incremento NTC 700 MW in una direzione e 500 MW nell’altra;
- **Milano–Montalto 355-P:** capacità superiore a 2 GW e creazione di una sezione diretta NORD–CSUD, oltre a un effetto su CSUD→CNOR;
- **Dorsale Ionica–Tirrenica 563-P:** oltre 2 GW su più sezioni; il pieno effetto Calabria–Sud è subordinato all’Ionian Link.

## 6. Interconnessioni estere

### Capacità totali DDS al 2030

| Frontiera | Import verso Italia GW | Export dall’Italia GW | Status |
| --- | --- | --- | --- |
| Francia | 4,5 | 2,2 | DDS 2030 |
| Svizzera | 4,6 | 1,9 | DDS 2030 |
| Austria | 0,7 | 0,3 | DDS 2030 |
| Slovenia | 1,1 | 1,2 | DDS 2030 |
| Montenegro | 0,6 | 0,6 | DDS 2030 |
| Grecia | 0,5 | 0,5 | DDS 2030 |
| Tunisia | 0,6 | 0,6 | DDS 2030 |
| Malta | 0,2 | 0,2 | DDS 2030 |

Questi valori sono un benchmark di scenario. Per un modello PyPSA-Eur europeo sono preferibilmente controlli di QA, perché le capacità transfrontaliere possono essere già rappresentate nella rete endogena.

### Incrementi di progetto PDS 2025

| ID | Progetto | Zona IT | Frontiera | Incremento NTC MW | E.E. | Stato | Condizione |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 167-P | Razionalizzazione Valchiavenna | NORD | Switzerland | 1.000 | post-2034 | Planning/consultation | — |
| 208-P | 132/110 kV Prati di Vizze–Steinach | NORD | Austria | 100 | post-2034 | Planned | — |
| 252-P | AT Dobbiaco–Austria | NORD | Austria | 160 | post-2034 | Planned | — |
| 204-P | 220 kV Italy–Austria interconnection | NORD | Austria | 500 | post-2034 | Planning/consultation | — |
| 200-I | Slovenia interconnection capacity increase — Phase 1 | NORD | Slovenia | 400 | post-2034 | Authorization/planning | Only Phase 1 of the 380/220-kV limitation-removal project. |
| 554-P | GRITA 2 | SUD | Greece | 1.000 | 2033/2035 first phase; full post-2034 | Authorization/consultation | — |
| 601-I | ELMED Italy–Tunisia | SICI | Tunisia | 600 | 2028 | Authorized / under development | Authorized May 2024. |
| 401-S | Italy–Montenegro second pole | CSUD | Montenegro | 600 | not specified | Authorized/construction, conditional | Conditional on authority opinion and Transbalkan corridor. |

Gli incrementi non sono assegnati automaticamente a entrambe le direzioni. La Tabella 1 non pubblica una matrice direzionale completa post-2030.

## 7. Varianti di rete

### 2040 Base

- capacità interne: S03 Figura 3, orizzonte 2040;
- topologia completa inclusiva di NORD–SUD, NORD–CSUD, CSUD–SICI e SARD–SICI;
- interconnessioni estere: preferibilmente dalla rete PyPSA-Eur; valori Terna come QA;
- nessuna duplicazione degli incrementi di progetto.

### 2040 Conservative

- capacità interne congelate al livello PDS 2025 del 2035;
- rappresenta ritardi dei progetti post-2035 senza introdurre probabilità arbitrarie;
- resta eseguibile e direttamente confrontabile con il Base.

### 2050 Central

- capacità interne 2040 Base mantenute come **floor documentato**;
- nessuna affermazione che tali valori costituiscano un target Terna 2050;
- estero preferibilmente endogeno PyPSA-Eur.

### 2050 High Grid

- variante predisposta ma non ancora numericamente chiusa;
- non viene applicato un incremento percentuale uniforme;
- richiede TYNDP, rapporto capacità obiettivo o altra evidenza ufficiale per corridoio.

## 8. Perdite e vincoli

Il corpus non fornisce un set completo e coerente di fattori di perdita per ogni frontiera e direzione. Per il modello spot base:

- `Link` interzonali senza perdita esplicita;
- perdite di rete trattate nella riconciliazione della domanda o come sensitivity;
- nessun uso del CAPEX di rete nell’ordine di dispacciamento;
- nessun vincolo dinamico o di system strength nel modello zonale;
- documentazione separata dei limiti non rappresentati.

## 9. QA

Sono stati creati **66 controlli**:

- **51 PASS**;
- **14 INFO**, relativi a revisioni DDS–PDS;
- **1 REVIEW**.

La review aperta è:

| QA | Tipo | Oggetto | Metrica | Nota |
| --- | --- | --- | --- | --- |
| QA3C-066 | Semantic/model-use control | 2050 | 2050 numeric capacity source | No complete official 2050 directional NTC matrix exists in the corpus; central case is a documented 2040 floor. |

Non risultano incoerenze che impediscano la Fase 3D.

## 10. Decisioni

| ID | Tema | Impostazione raccomandata | Bloccante |
| --- | --- | --- | --- |
| D3C-01 | Primary 2040 internal capacities | Use PDS 2025 S03 Figure 3 2040 directional limits, not the older DDS Figure 12 values. | Before scenario harmonisation, not before Phase 3D |
| D3C-02 | PyPSA component for zonal exchange | Use two directional Links per market border (or one controllable Link only where symmetric limits are guaranteed). | Before model build |
| D3C-03 | Foreign systems | Prefer endogenous neighbouring PyPSA-Eur buses; use Terna/DDS cross-border values as QA. Use collapsed external price buses only as fallback. | Before model build |
| D3C-04 | 2040 conservative network sensitivity | Use the S03 2035 directional capacities as the 2040 delayed-grid sensitivity. | No |
| D3C-05 | 2050 central network | Carry the PDS 2025 2040 network forward as a minimum floor, clearly labelled proxy, until an official 2050 zonal-capacity dataset is acquired. | Before 2050 final run |
| D3C-06 | 2050 High Grid sensitivity | Do not assign a generic percentage uplift. Populate only after TYNDP/target-capacity evidence or a documented corridor-specific rule. | Only for High Grid sensitivity |
| D3C-07 | Project nominal capacity vs NTC | Never convert project nominal MW directly into market-link p_nom. Use S03 Figure 3 or official transit-limit data. | No |
| D3C-08 | Network losses | Keep zonal Links lossless in the base price model unless a consistent corridor-loss dataset is obtained; test losses as a sensitivity. | Before model build |

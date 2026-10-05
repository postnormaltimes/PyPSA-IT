# MEM zonal capacity allocation methodology — 2040/2050

## Outcome

The six seven-zone installed-capacity tables are complete as a standalone scenario layer. They do not promote or edit the canonical v2.9 workbook and they do not run PyPSA.

## Corrected Terna CDP mapping

Direct visual inspection of Terna Figure 55 establishes PV 32/180, wind 17/66 and electrochemical storage 29/36. The inherited wind 29/66 and BESS 12/36 mapping is superseded. Figure 56 then uses an illustrative 15% rating for programmable capacity; MEM adopts the corresponding 0.85 available fraction for the Slow installed-capacity conversion.

The resulting Slow adjustments are:

- 2040 lost CDP: 11.231326599 GW; additional dispatchable capacity: 13.213325411 GW; Slow thermal total: 68.213325411 GW.
- 2050 lost CDP: 18.371377036 GW; additional dispatchable capacity: 21.613384748 GW; Slow programmable total: 51.613384748 GW.

## 2024 coal/oil removal mask

Terna Table 19 controls 4,917.4 MW coal and 1,975.9 MW petroleum products. The mask ties Terna NET province×Condensazione rows to Torrevaldaliga Nord, Federico II, Fiume Santo, Sulcis and San Filippo del Mela using operator evidence. Direct coal attribution is 4758.62 MW (96.77%); the 158.78-MW coal residual remains a NORD control band. Direct oil attribution is 886 MW (44.84%); the 1089.9-MW petroleum residual remains dispersed control bands across oil-compatible conversion rows. Residual bands are not plant records.

The interrupted all-steam/all-engine heuristic is not used. Fuel class and conversion technology remain separate. Fusina and Monfalcone are not asserted as direct 2024 coal capacity because operator evidence says coal service ended in 2023.

## 2040 Base/High

The mutually exclusive 2024 conversion matrix, after the reconciled coal/oil mask, plus direct geothermal, is 54.20886427 GW NET. It is scaled by 1.014594213339 to the 55-GW Terna envelope. Hydro is outside that envelope. Bioenergy source/fuel statistics are not separately added or subtracted; they remain embedded within conversion carriers in the 2040 anchor.

## 2050 programmable perimeter

MEM adopts geothermal inside the 30-GW efficient programmable benchmark. Terna's residual-load discussion subtracts programmable hydro before identifying the remaining need for programmable thermal and/or nuclear, and the 2050 balance refers to other programmable generation including programmable renewables. This is an explicit source-supported project perimeter decision, not a separately published geothermal line in Figure 55.

Therefore: 30 GW - 8 GW nuclear - 0.77179 GW geothermal = 21.22821 GW for the four PNIEC energy/CF-derived relative weights. The CFs create weights only and never constrain realized dispatch.

## Geography

- CCGT and GAS_CCS use combined surviving CCGT CHP+non-CHP 2024 geography.
- GT/OCGT and GAS_OTHER_FOSSIL use combined surviving GT CHP+non-CHP geography.
- Bioenergy and Bioenergy+CCS use the current renewable-source bioenergy geography only as a project allocation proxy.
- Geothermal retains current geography.
- Nuclear is fixed at 5 GW NORD and 3 GW CSUD.
- Slow increments preserve the corresponding Base non-nuclear technology weights and the same technology-specific zone shares.

## Hydro

Installed hydro is fixed at the current 23.294-GW fleet in every 2040/2050 scenario. The current class allocation is reconciled once to Terna controls and copied unchanged. The remaining PHS e_nom/pump-MW allocation, efficiencies, inflows, active reservoir energy and SOC conventions are operational solver-parameter gates, not capacity-table gates.

## Model guardrails

All capacities are fixed and non-extendable. Installed capacity does not prescribe annual generation. The DDS demand perimeter includes network losses, so primary internal commercial Links remain efficiency=1 unless demand is first netted of losses. The canonical v2.9 workbook remains unchanged and promotion remains blocked by its binary provenance gate.

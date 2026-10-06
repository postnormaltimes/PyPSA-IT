# Sources and data terms

Scenario quantities use explicit zonal data where available, followed by
regional or provincial aggregation. National quantities are disaggregated
only where the corresponding spatial key is documented.

- [Terna](https://www.terna.it/): *Prospettive di Sviluppo del Sistema Energetico
  2050* supplies the revised national solar, wind and nuclear capacities.
- [Terna/Snam DDS 2024](https://www.terna.it/it/sistema-elettrico/programmazione-territoriale-efficiente/scenari):
  scenario demand and the Figure 30 electrolyser distribution.
- [MASE](https://www.mase.gov.it/): PNIEC source for the earlier 2050 benchmark.
- [GME](https://www.mercatoelettrico.org/): market-zone definitions and market
  evidence cited in the model's source registers.
- [PyPSA-Eur](https://github.com/PyPSA/pypsa-eur) and
  [Atlite](https://github.com/PyPSA/atlite): spatial resource and runoff methods.

P2X uses the DDS 2040 DE-IT/GA-IT electrolyser vector: NORD/CNOR/CSUD/SUD/CALA/
SICI/SARD = 10/3/6/32/7/13/3 divided by 74. The same share applies to MW and
annual MWh in all six cases. For 2050 it is a documented spatial extrapolation
for broader hydrogen and synthetic-fuel P2X, not a published 2050 zonal vector.

Source PDFs, raw exports and research workbooks are not distributed with this
release. Model-ready contracts retain source identifiers and locators.
Source data remain subject to their original terms; MIT applies to software,
not to the original Terna, GME or MASE material. See the repository's
[third-party notices](../../../THIRD_PARTY_NOTICES.md).

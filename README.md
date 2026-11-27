# PyPSA-IT

How might Italian wholesale electricity prices change by 2040 and 2050? PyPSA-IT simulates a full year of electricity production, storage, trade and prices, hour by hour, across Italy’s seven bidding zones.

## Project video

https://github.com/user-attachments/assets/53c0ae03-93a7-4487-a80a-d4dee1370a70

[Download the video (MP4, 2 min 38 s)](Zonal/docs/media/italy-electricity-market-2040-2050.mp4?raw=true) | [Explore the figures and their data](Zonal/docs/FIGURES.md)

## What changes between 2040 and 2050?

In the **2040 Base** case, Italy still relies on gas alongside a much larger renewable fleet. Wind and solar supply 74% of domestic generation, and gas plants produce about 34 TWh over the year. By **2050**, gas generation falls to about 7 TWh in Base, while nuclear and bioenergy together supply 117 TWh. The annual national price falls even though winter electricity remains expensive. In the 2050 Base simulation, the national price averages €130/MWh in January and €25/MWh in June.

**Annual national wholesale price, weighted by electricity consumption (€/MWh)**

| Scenario | What changes | 2040 | 2050 |
|---|---|---:|---:|
| Base | Demand, generation, storage and grid capacity follow the project’s reference assumptions, drawn from Italian government and system-operator plans. | 83.90 | 58.41 |
| Infrastruttura Lenta | Demand follows Base, while wind, solar, batteries and grid upgrades develop more slowly. | 92.64 | 74.09 |
| Elettrificazione+ | Electricity demand grows faster than in Base, alongside greater renewable and battery capacity. | 85.69 | 69.32 |

National and local prices can move in opposite directions. In **Elettrificazione+**, the national average falls from €85.69 to €69.32/MWh between 2040 and 2050, while the average in the North rises from €91.50 to €96.08/MWh. The [annual zonal prices](Zonal/docs/figures/annual_zonal_prices.png) show how the seven markets compare.

These are scenario results. The annual price weights each zone and hour by electricity consumed, including electricity used for electrolysis. The model uses one historical weather year (2019) for both horizons. [Results and price definitions](Zonal/docs/RESULTS.md)

## Explore or reproduce the results

| To… | Start here |
|---|---|
| Inspect hourly prices, dispatch, storage and exchanges | [Download the six-case numerical results](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-final-results-v1) |
| Recreate the published charts | [Figure index with images, data tables and sources](Zonal/docs/FIGURES.md) |
| Run a scenario | [Prepared inputs](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-production-data-v3) and [step-by-step instructions](Zonal/docs/REPRODUCIBILITY_GUIDE.md) |
| Find the exact software version | [Versioned source release](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-model-v1.0.0) and [model version history](Zonal/docs/FINAL_VERSION_EVOLUTION.md) |

The [zonal model](Zonal/README.md) represents electricity exchanges between bidding zones. The scenarios specify how much generation, storage and grid capacity is available. The model then determines which plants operate and calculates hourly prices with those plant commitments fixed. A [nodal extension](Nodal/README.md) is planned to study the physical transmission network within each zone. [Methodology](Zonal/docs/METHODOLOGY.md) and [model limits](Zonal/docs/LIMITATIONS.md)

Emiliano Barin, Roberto Conoscenti, Maddalena Schena and Christian Bianchi developed the zonal study for the Master in Energy Management at POLIMI Graduate School of Management, supervised by Luca Pedicone. It received the **Best Project Work Award**.

The [MIT licence](LICENSE) covers the repository software. For datasets and figures, see the [source and reuse terms](THIRD_PARTY_NOTICES.md).

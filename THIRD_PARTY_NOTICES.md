# Third-party software and data

The repository's own software is licensed under [MIT](LICENSE). This licence
does not relicense third-party data, documents, geographical boundaries or
solver software.

## Modelling software

PyPSA-IT uses [PyPSA](https://github.com/PyPSA/PyPSA),
[PyPSA-Eur](https://github.com/PyPSA/pypsa-eur) methods and
[Atlite](https://github.com/PyPSA/atlite) resource conversion. PyPSA-Eur's MIT
copyright and permission notice is retained in
[LICENSES/PyPSA-Eur-MIT.txt](LICENSES/PyPSA-Eur-MIT.txt). Dependencies retain
their own licences. Gurobi requires a separately obtained licence; no licence
file or credential is distributed here.

## Energy-system sources

Scenario assumptions and derived contracts cite [Terna](https://www.terna.it/),
[GME](https://www.mercatoelettrico.org/) and [MASE](https://www.mase.gov.it/).
Their original material remains subject to its respective source terms.
The repository and prepared-input package contain model-ready transformations
and source references, not copies of the source PDFs, spreadsheets or raw
portal exports. The software licence does not grant redistribution rights to
those originals.

## Geography and weather

The optional plotting toolkit includes Natural Earth low-resolution map data,
which are [public domain](https://www.naturalearthdata.com/about/terms-of-use/).
Eurostat GISCO administrative boundaries are not redistributed. Obtain them
separately under [GISCO's terms](https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units),
including the required acknowledgement: © EuroGeographics for the administrative
boundaries. The optional geography downloader requires explicit acceptance of
those terms.

Prepared hourly profiles are model-derived series from the documented 2019
ERA5/SARAH3 resource conversion. Raw cutouts are not distributed. Original
weather products retain their [Copernicus](https://cds.climate.copernicus.eu/)
and [CM SAF](https://www.cmsaf.eu/) terms.

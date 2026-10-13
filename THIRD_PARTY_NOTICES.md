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
including the required acknowledgement: Â© EuroGeographics for the administrative
boundaries. The optional geography downloader requires explicit acceptance of
those terms.

Prepared hourly profiles are model-derived series from the documented 2019
ERA5/SARAH3 resource conversion. Raw cutouts are not distributed. Original
weather products retain their [Copernicus](https://cds.climate.copernicus.eu/)
and [CM SAF](https://www.cmsaf.eu/) terms.

## Numerical outputs and figure definitions

The final results package contains model-generated numerical outputs, compact verification evidence and source references. It does not contain raw source PDFs, private research workbooks, raw weather cutouts, restricted administrative geography or solver licences. Original/public hashes and transformations are documented in the package manifests. Appropriate source attribution remains required; the software MIT licence must not be interpreted as a new licence for third-party underlying data.

Thermal-unit research also used the accepted powerplantmatching 0.8.1 reference/derivation lineage. Prepared inputs retain derived synthetic-unit controls; the raw matched plant archive is not redistributed. Historical reconstruction may require separately obtained source material under its original terms.

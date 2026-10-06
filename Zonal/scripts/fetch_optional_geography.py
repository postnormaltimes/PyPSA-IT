"""Obtain optional map boundaries separately under GISCO's source terms."""
import argparse
from pathlib import Path
import urllib.request

URL = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/NUTS_RG_01M_2021_4326_LEVL_2.geojson"
TERMS = "https://ec.europa.eu/eurostat/web/gisco/geodata/statistical-units"
TARGET = Path(__file__).resolve().parents[1] / "optional/geography/NUTS_RG_01M_2021_4326_LEVL_2.geojson"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--accept-gisco-terms", action="store_true",
                        help=f"Confirm acceptance of the source terms: {TERMS}")
    args = parser.parse_args()
    if not args.accept_gisco_terms:
        parser.error(f"Read and accept the GISCO terms before download: {TERMS}")
    if TARGET.exists():
        raise FileExistsError("Existing geography is not overwritten")
    with urllib.request.urlopen(URL) as response:
        data = response.read()
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    with TARGET.open("xb") as stream:
        stream.write(data)
    print("© EuroGeographics for the administrative boundaries")
    print(TARGET.relative_to(Path(__file__).resolve().parents[1]))


if __name__ == "__main__":
    main()

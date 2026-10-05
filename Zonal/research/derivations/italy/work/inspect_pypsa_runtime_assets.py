from pathlib import Path

import pypsa
import xarray as xr


NETWORK = Path("mem-italy-market-model/data/reference/pypsa_it_2013_1w_input.nc")
RESOURCES = Path(
    r"source://pypsa-eur"
    r"\resources\italy_dispatch_2013_1w_europe_local_64"
)


def main() -> None:
    n = pypsa.Network(NETWORK)
    print("pypsa", pypsa.__version__)
    print("snapshots", len(n.snapshots), n.snapshots[0], n.snapshots[-1])
    print("bus_columns", n.buses.columns.tolist())
    cols = [
        c
        for c in ["country", "gme_zone", "x", "y", "carrier", "location", "candidate_id"]
        if c in n.buses
    ]
    print(n.buses.loc[n.buses["country"].eq("IT"), cols].to_string())
    print(
        "counts",
        {
            "buses": len(n.buses),
            "generators": len(n.generators),
            "loads": len(n.loads),
            "stores": len(n.stores),
            "storage_units": len(n.storage_units),
            "links": len(n.links),
            "lines": len(n.lines),
        },
    )
    for name in ["solar", "onwind", "offwind-ac", "offwind-dc"]:
        path = RESOURCES / f"profile_64_{name}.nc"
        ds = xr.open_dataset(path)
        print(name, dict(ds.sizes), ds.coords["bus"].values.tolist())
        ds.close()


if __name__ == "__main__":
    main()

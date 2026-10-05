from __future__ import annotations

import argparse
from pathlib import Path

import pypsa

from .common import QA, dump_json, ensure_output_dirs
from .validation import validate_network


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an unsolved MEM PyPSA network")
    parser.add_argument("network", type=Path)
    parser.add_argument("--year", type=int, required=True, choices=[2040, 2050])
    parser.add_argument("--scenario", required=True, choices=["Slow", "Base", "High"])
    parser.add_argument("--fixture", action="store_true")
    parser.add_argument("--no-assemble", action="store_true")
    args = parser.parse_args()
    ensure_output_dirs()
    network = pypsa.Network(args.network)
    qa = validate_network(network, args.year, args.scenario, fixture=args.fixture, assemble_model=not args.no_assemble)
    stem = f"MEM_{args.year}_{args.scenario.upper()}_{len(network.snapshots)}h_PRE_SOLVE_QA"
    qa.to_csv(QA / f"{stem}.csv", index=False)
    dump_json(
        QA / f"{stem}.json",
        {
            "status": "PASS" if qa["status"].eq("PASS").all() else "FAIL",
            "checks": len(qa),
            "failures": int(qa["status"].eq("FAIL").sum()),
            "solver_invoked": False,
            "fixture": args.fixture,
        },
    )
    print(qa.to_string(index=False))
    if qa["status"].eq("FAIL").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()


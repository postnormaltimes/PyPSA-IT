from __future__ import annotations

import argparse
from .common import ACCEPTED_RUNTIME, FIXTURE_RUNTIME, NETWORKS, dump_json, ensure_output_dirs
from .network import build_network


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an unsolved MEM PyPSA network")
    parser.add_argument("--year", type=int, required=True, choices=[2040])
    parser.add_argument("--scenario", required=True, choices=["Slow", "Base", "High"])
    parser.add_argument("--fixture-hours", type=int, choices=[24, 168])
    parser.add_argument("--assemble-model", action="store_true", help="Assemble Linopy constraints without solving")
    args = parser.parse_args()
    ensure_output_dirs()
    if args.fixture_hours:
        runtime_dir = FIXTURE_RUNTIME / f"h{args.fixture_hours}"
        output_dir = NETWORKS / "synthetic_fixture_not_model_input"
    else:
        runtime_dir = ACCEPTED_RUNTIME
        output_dir = NETWORKS / "accepted"
    network, metadata = build_network(runtime_dir.resolve(), args.year, args.scenario)
    if args.assemble_model:
        model = network.optimize.create_model(include_objective_constant=False)
        metadata["linopy_model_assembled"] = True
        metadata["linopy_variables"] = len(model.variables)
        metadata["linopy_constraints"] = len(model.constraints)
        metadata["solver_invoked"] = False
    else:
        metadata["linopy_model_assembled"] = False
    output_dir.mkdir(parents=True, exist_ok=True)
    stem = f"MEM_{args.year}_{args.scenario.upper()}_{len(network.snapshots)}h_UNSOLVED"
    network.export_to_netcdf(output_dir / f"{stem}.nc")
    dump_json(output_dir / f"{stem}.json", metadata)
    print(output_dir / f"{stem}.nc")


if __name__ == "__main__":
    main()

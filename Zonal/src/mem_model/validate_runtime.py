from __future__ import annotations

import argparse
from pathlib import Path

from .runtime_bundle import validate_runtime_bundle
from .stage_b_2040_runtime import ACCEPTED_RUNTIME, validate_runtime, verify_accepted_runtime_manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a complete MEM hourly source bundle before promotion")
    parser.add_argument("runtime_dir", type=Path)
    parser.add_argument("--fixture", action="store_true", help="Allow a declared short synthetic fixture")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    runtime_dir = args.runtime_dir.resolve()
    if runtime_dir == ACCEPTED_RUNTIME.resolve() and not args.fixture:
        verify_accepted_runtime_manifest(runtime_dir)
        qa = validate_runtime(runtime_dir)
    else:
        qa = validate_runtime_bundle(runtime_dir, require_complete_year=not args.fixture)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        qa.to_csv(args.output, index=False)
    print(qa.to_string(index=False))
    if qa["status"].eq("FAIL").any():
        raise SystemExit(1)


if __name__ == "__main__":
    main()

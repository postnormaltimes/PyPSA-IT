"""Restore the prepared Zonal inputs, checking every byte before writing."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def restore(source: Path, root: Path = ROOT, package: str = "inputs") -> dict:
    manifests = root / "inputs/manifests"
    if package not in {"inputs", "results"}:
        raise ValueError("Unknown data package")
    release_name = "release_assets.json" if package == "inputs" else "results_assets.json"
    table_name = "data_package.csv" if package == "inputs" else "results_package.csv"
    release = json.loads((manifests / release_name).read_text())
    table_bytes = (manifests / table_name).read_bytes()
    if digest(table_bytes) != release["data_manifest_sha256"]:
        raise RuntimeError("Data manifest hash mismatch")
    with (manifests / table_name).open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    asset, = release["assets"]
    archive = source / asset["filename"]
    if archive.stat().st_size != asset["size_bytes"] or digest(archive.read_bytes()) != asset["sha256"]:
        raise RuntimeError("Prepared-input archive hash/size mismatch")
    root = root.resolve()
    pending = []
    prefix = release.get("member_prefix", "path_a") + "/"
    with zipfile.ZipFile(archive) as zipped:
        expected = {prefix + row["destination"] for row in rows}
        if len(rows) != len(expected) or set(zipped.namelist()) != expected or len(zipped.namelist()) != len(expected):
            raise RuntimeError("Archive file list differs from the manifest")
        for row in rows:
            relative = PurePosixPath(row["destination"])
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative) or ":" in str(relative):
                raise RuntimeError("Unsafe destination")
            if relative.suffix.lower() == ".lic" or any(part.lower() in {".git", ".venv", ".env", "gurobi.lic"} for part in relative.parts):
                raise RuntimeError("Forbidden private destination")
            target = (root / str(relative)).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError("Destination leaves the project")
            data = zipped.read(prefix + str(relative))
            if len(data) != int(row["size_bytes"]) or digest(data) != row["sha256"]:
                raise RuntimeError("Prepared-input member hash/size mismatch")
            if target.exists():
                if not target.is_file() or digest(target.read_bytes()) != row["sha256"]:
                    raise RuntimeError(f"Refusing to overwrite a different file: {relative}")
            else:
                pending.append((target, data))
        # All hashes and existing destinations have been checked before writing.
        for target, data in pending:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(data)
    return {"status": "PASS", "files_verified": len(rows), "files_restored": len(pending),
            "model_constructed": False, "solver_invocations": 0}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--package", choices=("inputs", "results"), default="inputs")
    args = parser.parse_args()
    print(json.dumps(restore(args.source_dir, package=args.package), indent=2))

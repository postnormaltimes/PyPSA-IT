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


def restore(source: Path, root: Path = ROOT) -> dict:
    manifests = root / "inputs/manifests"
    release = json.loads((manifests / "release_assets.json").read_text())
    table_bytes = (manifests / "data_package.csv").read_bytes()
    if digest(table_bytes) != release["data_manifest_sha256"]:
        raise RuntimeError("Data manifest hash mismatch")
    with (manifests / "data_package.csv").open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    asset, = release["assets"]
    archive = source / asset["filename"]
    if archive.stat().st_size != asset["size_bytes"] or digest(archive.read_bytes()) != asset["sha256"]:
        raise RuntimeError("Prepared-input archive hash/size mismatch")
    root = root.resolve()
    pending = []
    with zipfile.ZipFile(archive) as zipped:
        expected = {"path_a/" + row["destination"] for row in rows}
        if len(rows) != len(expected) or set(zipped.namelist()) != expected or len(zipped.namelist()) != len(expected):
            raise RuntimeError("Archive file list differs from the manifest")
        for row in rows:
            relative = PurePosixPath(row["destination"])
            if relative.is_absolute() or ".." in relative.parts or "\\" in str(relative) or ":" in str(relative):
                raise RuntimeError("Unsafe destination")
            target = (root / str(relative)).resolve()
            if not target.is_relative_to(root):
                raise RuntimeError("Destination leaves the project")
            data = zipped.read("path_a/" + str(relative))
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
    print(json.dumps(restore(parser.parse_args().source_dir), indent=2))

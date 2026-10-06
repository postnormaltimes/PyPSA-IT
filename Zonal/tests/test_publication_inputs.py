"""Prepared-input portability and bootstrap safety; no optimisation."""
import csv
import importlib.util
import json
from pathlib import Path
import zipfile

import pandas as pd
import pytest

from mem_model import stage_b_uc2 as uc2
from mem_model.common import ROOT, sha256_file
from mem_model.stage_b_zonal_vre import no_models_or_solves


def bootstrap_module():
    spec = importlib.util.spec_from_file_location("bootstrap_data", ROOT / "scripts/bootstrap_data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def sample_package(tmp_path):
    root, source = tmp_path / "project", tmp_path / "source"
    manifests = root / "inputs/manifests"
    manifests.mkdir(parents=True)
    source.mkdir()
    module = bootstrap_module()
    rows = [{"destination": f"inputs/{name}.csv", "sha256": module.digest(data), "size_bytes": len(data)}
            for name, data in (("one", b"value\n1\n"), ("two", b"value\n2\n"))]
    with (manifests / "data_package.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader(); writer.writerows(rows)
    archive = source / "inputs.zip"
    with zipfile.ZipFile(archive, "w") as zipped:
        for row, data in zip(rows, (b"value\n1\n", b"value\n2\n")):
            zipped.writestr("path_a/" + row["destination"], data)
    release = {"data_manifest_sha256": module.digest((manifests / "data_package.csv").read_bytes()),
               "assets": [{"filename": archive.name, "size_bytes": archive.stat().st_size,
                           "sha256": module.digest(archive.read_bytes())}]}
    (manifests / "release_assets.json").write_text(json.dumps(release))
    return module, root, source


def test_bootstrap_restores_exact_bytes_and_is_idempotent(sample_package):
    module, root, source = sample_package
    assert module.restore(source, root)["files_restored"] == 2
    assert module.restore(source, root)["files_restored"] == 0
    assert (root / "inputs/one.csv").read_bytes() == b"value\n1\n"


def test_bootstrap_rejects_overwrite_before_any_write(sample_package):
    module, root, source = sample_package
    (root / "inputs/two.csv").write_bytes(b"protected")
    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        module.restore(source, root)
    assert not (root / "inputs/one.csv").exists()
    assert (root / "inputs/two.csv").read_bytes() == b"protected"


def test_bootstrap_rejects_archive_corruption(sample_package):
    module, root, source = sample_package
    with (source / "inputs.zip").open("ab") as stream:
        stream.write(b"unexpected")
    with pytest.raises(RuntimeError, match="archive hash/size"):
        module.restore(source, root)
    assert not (root / "inputs/one.csv").exists()


def test_all_distributed_members_match_the_publication_manifest():
    rows = pd.read_csv(ROOT / "inputs/manifests/data_package.csv")
    assert rows.destination.is_unique
    assert len(rows.loc[rows.destination.str.endswith(".nc")]) == 18
    assert all(sha256_file(ROOT / row.destination) == row.sha256 for row in rows.itertuples())
    assert not any("results/" in path or "runtime_sources/" in path for path in rows.destination)


def test_plotting_dependency_resolves_independently_of_working_directory(tmp_path, monkeypatch):
    import yaml
    from mem_model.visualization.vis_x1 import CONFIG, _toolkit
    monkeypatch.chdir(tmp_path)
    assert len(_toolkit(yaml.safe_load(CONFIG.read_text()))) == 8


@pytest.mark.parametrize("year,scenario", uc2.SCENARIOS)
def test_all_six_publication_static_preflights_without_results(year, scenario):
    with no_models_or_solves(), uc2.execution_variant("final_v4"):
        check = uc2.preflight(year, scenario, persist=False)
    assert check["status"] == "PASS"
    assert check["optimization_model_constructed"] is False
    assert check["manual_authorized"] is True
    assert not (ROOT / "results").exists()

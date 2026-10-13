"""Saved-result publication owner. No model construction or result re-export.

Use ``python -m mem_model.reporting.publication --help``. The optional complete
command wraps the existing ninth final-QA stage; standalone rendering never
reruns that stage or rewrites any original receipt.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import html
import json
import os
from pathlib import Path
from unittest.mock import patch

import pandas as pd
import yaml

from ..common import ROOT, sha256_file

CONFIG = ROOT / "config/publication.yaml"
SCHEMA = "MEM_FINAL_PUBLICATION_V1"


def _read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=False,
        default=lambda v: v.item() if hasattr(v, "item") else str(v)), encoding="utf-8")
    os.replace(temporary, path)


def configuration(path=CONFIG):
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if cfg["schema"] != SCHEMA or len(cfg["figures"]) != 11:
        raise ValueError("PUBLICATION_CONFIG_SCHEMA")
    if len({item["id"] for item in cfg["figures"]}) != 11:
        raise ValueError("PUBLICATION_DUPLICATE_FIGURE_ID")
    owner = (ROOT / cfg["owner"]).resolve()
    if not owner.is_relative_to((ROOT / "results/publication").resolve()):
        raise ValueError("PUBLICATION_OWNER_OUTSIDE_PUBLICATION_ROOT")
    cfg["_config"] = str(Path(path).resolve())
    cfg["_owner"] = owner
    return cfg


def _identity(path):
    path = Path(path).resolve()
    return path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else str(path)


def _path(value):
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


class Sources:
    """Match saved receipt/manifest pins, including old Windows separators."""
    def __init__(self, contract=None):
        self.contract = contract or {}
        self.pins = {}
        self.hashes = {}
        self.networks = {}
        self.cases = {}

    def sha(self, path):
        path = Path(os.path.abspath(path))
        if path.suffix.lower() == ".nc":
            raise ValueError("PUBLICATION_NETWORK_READ_FORBIDDEN")
        if path not in self.hashes:
            self.hashes[path] = sha256_file(path)
        return self.hashes[path]

    def pin(self, path, expected):
        path = Path(os.path.abspath(path))
        old = self.pins.get(path)
        if old is not None and old != expected:
            raise ValueError("PUBLICATION_CONFLICTING_SOURCE_PINS: " + _identity(path))
        self.pins[path] = expected

    def map(self, values, base):
        for name, digest in values.items():
            path = Path(name.replace("\\", "/"))
            path = path if path.is_absolute() else (ROOT / path if path.as_posix().startswith("data/research_results/") else Path(base) / path)
            self.pin(path, digest)

    def verify(self, path):
        path = Path(os.path.abspath(path))
        if path not in self.pins:
            raise ValueError("PUBLICATION_UNREGISTERED_SOURCE: " + _identity(path))
        if self.sha(path) != self.pins[path]:
            raise ValueError("PUBLICATION_SOURCE_HASH_MISMATCH: " + _identity(path))
        return self.sha(path)

    def manifest(self, path, base):
        self.verify(path)
        for row in pd.read_csv(path).to_dict("records"):
            name = row["artifact"].replace("\\", "/")
            path = _path(name) if name.startswith("data/research_results/") else Path(base) / name
            if not path.exists() and name.startswith("CANONICAL/"):
                path = Path(base).parent / name
            self.pin(path, row["sha256"])


def _authority(case, sources):
    report = case["report"]
    qa_path = report.parent / "QA/FINAL_SCENARIO_QA.json"
    qa = _read(qa_path)
    if qa.get("status") != "PASS" or qa.get("year", case["year"]) != case["year"] or qa.get("scenario", case["scenario"]) != case["scenario"]:
        raise ValueError("PUBLICATION_FINAL_QA_NOT_PASS: " + str(report))
    sources.pin(qa_path, sources.sha(qa_path))
    r1_path = report / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json"
    receipt = _read(r1_path)
    if receipt.get("status") != "PASS" or (receipt["year"], receipt["scenario"]) != (case["year"], case["scenario"]):
        raise ValueError("PUBLICATION_REPORTING_SOURCE_CONFLICT")
    if receipt["physical_source"] != "UC_MILP" or receipt["price_source"] != "FIXED_COMMITMENT_PRICE_LP":
        raise ValueError("PUBLICATION_RESULT_AUTHORITY_CONFLICT")
    sources.pin(r1_path, sources.sha(r1_path))
    for name in ("original_artifact_hashes", "artifact_hashes"):
        sources.map(receipt.get(name, {}), report)
    sources.map(receipt.get("protected_source_hashes", {}), ROOT)
    register = report / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv"
    # Earlier R1 receipts do not hash the register; its contents are independently
    # checked against each solve + verification receipt, then frozen here.
    sources.pin(register, sources.sha(register))
    rows = pd.read_csv(register)
    identities = {}
    solves = {}
    for role in ("CANONICAL_PHYSICAL", "CANONICAL_PRICES_DUALS_ONLY"):
        selected = rows[rows.current_role == role]
        if len(selected) != 1:
            raise ValueError("PUBLICATION_AUTHORITY_ROLE_NOT_UNIQUE")
        row = selected.iloc[0]
        solve_path, verify_path = _path(row.receipt), _path(row.manifest)
        for path in (solve_path, verify_path):
            sources.verify(path)
        solve, verification = _read(solve_path), _read(verify_path)
        kind = "UC_MILP" if role == "CANONICAL_PHYSICAL" else "FIXED_COMMITMENT_PRICE_LP"
        if (row.solved_or_unsolved != "SOLVED" or solve.get("kind") != kind
                or solve.get("job_id") != row.result_id or verification.get("job_id") != row.result_id
                or (solve.get("year"), solve.get("scenario")) != (case["year"], case["scenario"])
                or (verification.get("year"), verification.get("scenario")) != (case["year"], case["scenario"])):
            raise ValueError("PUBLICATION_SOLVE_ROLE_OR_CASE_CONFLICT")
        digest = row.network_sha256
        if solve["status"] != "PASS" or verification["status"] != "PASS" or solve["solved_sha256"] != digest or verification["solved_sha256"] != digest:
            raise ValueError("PUBLICATION_SOLVE_VERIFY_LINEAGE_CONFLICT")
        if solve["snapshots"] != 8760 or row.year != case["year"] or row.scenario != case["scenario"]:
            raise ValueError("PUBLICATION_CASE_CHRONOLOGY_CONFLICT")
        network = _path(row.network_path).resolve()
        if sources.contract:
            identity = sources.contract["solved_network_identities"].get(_identity(network))
            if not identity or identity["sha256"] != digest or identity["year"] != case["year"] or identity["scenario"] != case["scenario"] or identity["authority"] != role:
                raise ValueError("PUBLIC_RESULTS_NETWORK_IDENTITY_CONFLICT")
            sources.networks[_identity(network)] = dict(identity, distribution="RECEIPT_IDENTITY_ONLY")
        else:
            stat = network.stat()  # Receipt identity; no .nc contents read.
            sources.networks[_identity(network)] = dict(sha256=digest, bytes=stat.st_size,
                mtime_ns=stat.st_mtime_ns, authority=role)
        identities[role] = digest
        solves[role] = (solve, verification)
    lp_solve, lp_verify = solves["CANONICAL_PRICES_DUALS_ONLY"]
    if lp_solve.get("fixed_commitment_milp_sha256") != identities["CANONICAL_PHYSICAL"] or lp_verify.get("milp_solved_sha256") != identities["CANONICAL_PHYSICAL"]:
        raise ValueError("PUBLICATION_PRICING_LP_UC_PARENT_CONFLICT")
    for name in ("MEM_CANONICAL_REPORTING_MANIFEST.csv", "MEM_CANONICAL_REPORTING_R1_MANIFEST.csv"):
        manifest = report / "CANONICAL" / name
        if manifest.exists():
            sources.manifest(manifest, report / "CANONICAL")
    contract = report / "uc2_REPORTING_AUTHORITY_CONTRACT.json"
    sources.pin(contract, sources.sha(contract))
    data = _read(contract)
    if data["physical_source"] != "VERIFIED_UC_MILP" or data["price_source"] != "VERIFIED_FIXED_COMMITMENT_LP_DUALS" or data["pricing_LP_primal"] != "NOT_CANONICAL":
        raise ValueError("PUBLICATION_AUTHORITY_CONTRACT_CONFLICT")
    case["authority"] = dict(uc_sha256=identities["CANONICAL_PHYSICAL"],
        lp_sha256=identities["CANONICAL_PRICES_DUALS_ONLY"], original_result_status=receipt["result_status"],
        qa=_identity(qa_path), price_weight=data["canonical_price_weight"])
    case["receipt"] = receipt
    return case


def _regime(case, cfg, sources):
    key = f"{case['year']}_{case['scenario']}"
    override = cfg.get("evidence_overrides", {}).get(key, {})
    report = case["report"]
    # Prefer a newly persisted same-run canonical bundle. The explicit accepted
    # supplement is only for the historical case whose primary bundle is absent;
    # a present but conflicting canonical bundle must fail, never fall back.
    if not (report / "MEM_MARGINAL_REGIME_RECEIPT.json").exists() and override:
        report = ROOT / override["regime_report"]
    receipt_path = report / "MEM_MARGINAL_REGIME_RECEIPT.json"
    receipt = _read(receipt_path)
    if receipt.get("status") != "PASS_CANONICAL_MARGINAL_REGIME" or receipt.get("methodology") != "LEXICOGRAPHIC_REGIME_V1" or receipt["model_year"] != case["year"] or receipt["scenario"] != case["scenario"]:
        raise ValueError("PUBLICATION_MARGINAL_REGIME_CONFLICT")
    lp = case["authority"]["lp_sha256"]
    lp_hashes = [digest for name, digest in receipt["source_hashes"].items()
                 if "FIXED_COMMITMENT_PRICE_LP" in name and name.lower().endswith(".nc")]
    if lp_hashes != [lp]:
        raise ValueError("PUBLICATION_REGIME_FIXED_LP_LINEAGE_MISMATCH")
    sources.pin(receipt_path, sources.sha(receipt_path))
    sources.map(receipt["source_hashes"], ROOT)
    sources.map(receipt["artifact_hashes"], report)
    for name in receipt["source_hashes"]:
        path = _path(name)
        # Inherit validated KKT extraction; publication does not consume or
        # revalidate the multi-GB raw LP arrays/candidate population. Verify
        # controlling QA/methodology/prices/receipts and the tables actually
        # plotted below, retaining every upstream pin in the original receipt.
        if path.suffix.lower() in {".json", ".csv"}:
            sources.verify(path)
    data = report / "CANONICAL/marginal_regime/data"
    qa_path = data / f"lexicographic_regime_QA_{case['year']}_{case['scenario']}.json"
    sources.verify(qa_path)
    qa = _read(qa_path)
    if qa["status"] != "PASS_CANONICAL_MARGINAL_REGIME" or qa["rows"] != 61320 or qa["snapshots"] != 8760:
        raise ValueError("PUBLICATION_REGIME_COMPLETENESS_FAILURE")
    case["regime_data"] = data
    case["regime_receipt"] = receipt_path
    return case


def case_source(cfg, sources, year, scenario, variant=None, result_root=None, regime=True):
    year = int(year)
    baseline = cfg["baselines"][year]
    root = ROOT / (result_root or baseline["result_root"])
    key = (year, scenario, str(root), regime)
    if key in sources.cases:
        return sources.cases[key]
    case = dict(year=year, scenario=scenario, variant=variant or baseline["variant"],
        report=(root / str(year) / scenario / "REPORTING").resolve())
    case = _authority(case, sources)
    case["public_saved_tables_only"] = bool(sources.contract)
    result = _regime(case, cfg, sources) if regime else case
    sources.cases[key] = result
    return result


def available_cases(cfg, sources, years=None):
    cases, missing = [], []
    for year, baseline in cfg["baselines"].items():
        if years and int(year) not in years:
            continue
        for scenario in cfg["scenarios"]:
            qa = ROOT / baseline["result_root"] / str(year) / scenario / "QA/FINAL_SCENARIO_QA.json"
            if not qa.exists() or _read(qa).get("status") != "PASS":
                missing.append(dict(year=year, scenario=scenario, status="WAITING_FOR_FINAL_QA"))
            else:
                cases.append(case_source(cfg, sources, year, scenario))
    return cases, missing


def historical_cases(cfg, sources, year):
    result = []
    for version in cfg["historical_versions"][year]:
        for scenario in cfg["scenarios"]:
            if version["status"] == "PREPARED_NOT_EXECUTED":
                result.append(dict(year=year, scenario=scenario, variant=version["variant"],
                    report=None, status=version["status"], note=version["note"]))
            else:
                result.append(case_source(cfg, sources, year, scenario,
                    version["variant"], version["result_root"], regime=False))
    return result


@contextmanager
def reporting_guard():
    """Headless reporting: fail before network reads, model construction or solve."""
    import pypsa
    import xarray as xr
    from .uc2_postprocess import no_solver_calls
    counts = {"network_opens": 0, "model_result_regeneration": 0}
    def forbidden(*args, **kwargs):
        counts["network_opens"] += 1
        raise RuntimeError("PUBLICATION_NETWORK_OPEN_FORBIDDEN")
    with no_solver_calls() as solver, patch.object(pypsa.Network, "__init__", forbidden), patch.object(xr, "open_dataset", forbidden):
        yield counts, solver


def _code_pins(cfg):
    names = ["src/mem_model/reporting/publication.py",
        "src/mem_model/visualization/publication_figures.py",
        "src/mem_model/visualization/publication_price_atlas.py",
        "src/mem_model/visualization/publication_geography.py",
        "src/mem_model/visualization/vis_x1_reference_42bus.py",
        "src/mem_model/visualization/publication_dispatch.py",
        "src/mem_model/reporting/canonical_results.py",
        "src/mem_model/reporting/marginal_setter_lexicographic.py",
        "src/mem_model/reporting/dispatch_figures.py",
        "src/mem_model/visualization/dispatch_presentation.py",
        "src/mem_model/visualization/uc2_vis_x1.py",
        "src/mem_model/visualization/uc2_comparison.py",
        "src/mem_model/visualization/vis_x1.py",
        "src/mem_model/stage_b_uc2.py",
        "src/mem_model/reporting/uc2_postprocess.py",
        "src/mem_model/reporting/uc2_diagnostics.py",
        "src/mem_model/visualization/marginal_setter_lexicographic.py",
        "config/stage_b_reporting.yaml", "config/vis_x1.yaml", "config/marginal_regime.yaml", "pyproject.toml"]
    paths = [ROOT / name for name in names] + [Path(cfg["_config"])]
    vis = yaml.safe_load((ROOT / "config/vis_x1.yaml").read_text(encoding="utf-8"))
    handoff = (ROOT / Path(vis["external_reference"]))
    manifest = handoff / "TRANSFER_MANIFEST.json"
    paths.append(manifest)
    sealed = _read(manifest)
    # The shared style imports the sealed adapter's recovered zone palette.
    # Its bytes must govern cached reuse as well as fresh rendering.
    for section in ("toolkit_file_hashes", "adapter_file_hashes"):
        for name, expected in sealed[section].items():
            path = handoff / name
            if sha256_file(path) != expected:
                raise ValueError("PUBLICATION_SEALED_TOOLKIT_CHANGED: " + name)
            paths.append(path)
    return {_identity(path): sha256_file(path) for path in paths}


def _environment():
    import matplotlib
    import numpy
    import pyarrow
    from matplotlib import font_manager
    font = "Aptos" if any(f.name == "Aptos" for f in font_manager.fontManager.ttflist) else "DejaVu Sans"
    path = font_manager.findfont(font)
    return dict(matplotlib=matplotlib.__version__, pandas=pd.__version__, numpy=numpy.__version__,
        pyarrow=pyarrow.__version__, PyYAML=yaml.__version__, font=font,
        font_sha256=sha256_file(Path(path)), font_bundled=False)


def _fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _figure_def(cfg, identifier):
    return next(item for item in cfg["figures"] if item["id"] == identifier)


def _render(identifier, cases, history, references, presentation=None):
    if identifier.startswith("ATLAS:"):
        from ..visualization.publication_price_atlas import render
        _, view, zone = identifier.split(":")
        return render(view, cases, zone)
    if identifier.startswith("GEOGRAPHY:"):
        from ..visualization.publication_geography import render
        return render(identifier.split(":")[1], cases)
    if identifier in ("6B", "6C"):
        from ..visualization.publication_dispatch import render_daily, render_peak_week
        return render_daily(cases) if identifier == "6B" else render_peak_week(cases, reference_cases=references)
    from ..visualization.publication_figures import render
    return render(identifier, cases, historical=history, presentation=presentation)


def _save(cfg, sources, folder, definition, cases, history=None, references=None):
    """Source + code hash cache; output hashes are checked before reuse."""
    folder = Path(folder)
    name, identifier = definition["name"], definition["id"]
    metadata = folder / f"{name}.json"
    code = _code_pins(cfg)
    environment = _environment()
    for key, digest in code.items():
        sources.pin(_path(key), digest)
    if metadata.exists():
        prior = _read(metadata)
        source_hashes = prior["source_hashes"]
        valid = prior["code_hashes"] == code and prior.get("render_environment") == environment
        for key, digest in source_hashes.items():
            path = _path(key)
            if not path.exists() or sources.sha(path) != digest:
                valid = False
        for key, digest in prior["artifact_hashes"].items():
            path = folder / key
            if not path.exists() or sources.sha(path) != digest:
                valid = False
        if valid:
            return prior, False
    rendered = _render(identifier, cases, history, references, cfg.get("presentation"))
    source_paths = {Path(path).resolve() for path in rendered["sources"]}
    all_cases = list(cases) + [c for c in (history or []) if c.get("report")] + list(references or [])
    for case in all_cases:
        source_paths.update((case["report"] / "MEM_UC2_VIS_REPORTING_R1_RECEIPT.json",
            case["report"] / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv",
            case["report"].parent / "QA/FINAL_SCENARIO_QA.json"))
        for role in ("CANONICAL_PHYSICAL", "CANONICAL_PRICES_DUALS_ONLY"):
            row = pd.read_csv(case["report"] / "uc2_VIS_RESULT_AUTHORITY_REGISTER.csv")
            row = row[row.current_role == role].iloc[0]
            source_paths.update((_path(row.receipt), _path(row.manifest)))
        if identifier == "9":
            source_paths.add(case["regime_receipt"])
    hashes = {}
    for path in sorted(source_paths):
        # Producer-owned cached presentation artifacts carry their own accepted
        # receipt; those pins are validated by produce_dispatch_figures itself.
        if path not in sources.pins and "DISPATCH_PRESENTATION" in path.parts:
            sources.pin(path, sources.sha(path))
        hashes[_identity(path)] = sources.verify(path)
    folder.mkdir(parents=True, exist_ok=True)
    fig = rendered["figure"]
    import matplotlib as mpl
    import matplotlib.pyplot as plt
    data_path = folder / f"{name}.csv"
    rendered["data"].to_csv(data_path, index=False)
    with mpl.rc_context({"svg.hashsalt": SCHEMA, "savefig.facecolor": "white"}):
        fig.savefig(folder / f"{name}.png", dpi=cfg["render"]["dpi"], facecolor="white", metadata={"Software": SCHEMA})
        fig.savefig(folder / f"{name}.svg", facecolor="white", metadata={"Date": None, "Creator": SCHEMA})
    plt.close(fig)
    artifacts = {f"{name}.{ext}": sha256_file(folder / f"{name}.{ext}") for ext in ("png", "svg", "csv")}
    for artifact_name, digest in artifacts.items():
        sources.hashes[Path(os.path.abspath(folder / artifact_name))] = digest
    qa = rendered["qa"]
    value = dict(schema=SCHEMA, figure_id=identifier, name=name,
        status="PUBLICATION_COPY_AWAITING_EDITORIAL_REVIEW", publication_baselines=cfg["baselines"],
        cases=[dict(year=c["year"], scenario=c["scenario"], variant=c["variant"], **c["authority"]) for c in cases],
        caption=rendered["caption"], analytical_purpose=definition["purpose"],
        required_qa=definition["qa"], producer=definition["producer"], qa=qa,
        source_hashes=hashes, code_hashes=code, render_environment=environment,
        source_fingerprint=_fingerprint([hashes, code, environment]),
        artifact_hashes=artifacts, solver_invocations=0, network_opens=0,
        physical_source="UC_MILP", price_source="FIXED_COMMITMENT_LP_DUALS",
        original_qa_and_statuses_preserved=True, same_dataframe_png_svg_csv=True)
    _json(metadata, value)
    return value, True


def _index(cfg):
    """One asset owner; galleries link to renders rather than copy graphics."""
    owner = cfg["_owner"]
    rows, cards = [], []
    current = check(cfg)
    states = {(row["scope"], row["name"]): row["status"] for row in current["figures"]}
    for path in sorted(owner.rglob("*.json")):
        if {"editorial_review", "zonal_atlas"}.intersection(path.relative_to(owner).parts):
            continue
        if path.name in {"PUBLICATION_RECEIPT.json", "PROTECTED_ARTIFACTS.json"}:
            continue
        value = _read(path)
        if value.get("schema") != SCHEMA or "figure_id" not in value:
            continue
        rel = path.parent.relative_to(owner)
        state = states.get((rel.as_posix(), value["name"]), "UNREGISTERED")
        files = {ext: (rel / f"{value['name']}.{ext}").as_posix() for ext in ("png", "svg", "csv")}
        rows.append(dict(figure_id=value["figure_id"], name=value["name"], scope=rel.as_posix(),
            analytical_purpose=value["analytical_purpose"], producer=value["producer"],
            source_authority="Verified UC physical quantities; same-run fixed-LP prices / KKT roots",
            applicable_cases="; ".join(f"{c['year']} {c['scenario']} {c['variant']}" for c in value["cases"]),
            png=files["png"], svg=files["svg"], data=files["csv"], metadata=path.relative_to(owner).as_posix(),
            required_qa=value["required_qa"], status=state, editorial_status=value["status"]))
        cards.append((rel.as_posix(), f'<article><h3>Figure {html.escape(value["figure_id"])} — {html.escape(value["analytical_purpose"])}</h3>'
            f'<p>{html.escape(rel.as_posix())} · <strong>{state}</strong></p><a href="{files["png"]}"><img loading="lazy" src="{files["png"]}" alt="{html.escape(value["name"])}"></a>'
            f'<p>{html.escape(value["caption"])}</p><p><a href="{files["svg"]}">SVG</a> · <a href="{files["csv"]}">CSV</a> · <a href="{path.relative_to(owner).as_posix()}">QA / sources</a></p></article>'))
    pd.DataFrame(rows).to_csv(owner / "PUBLICATION_FIGURE_REGISTER.csv", index=False)
    principal, supplementary = [], []
    for row, (scope, card) in zip(rows, cards):
        if scope == "combined" or row["figure_id"] in ("1", "2"):
            principal.append((row["figure_id"], card))
        else:
            supplementary.append((scope, card))
    principal.sort(key=lambda pair: [d["id"] for d in cfg["figures"]].index(pair[0]))
    body = ["<h2>Curated publication set — 11 figure types</h2>" + "".join(card for _, card in principal)]
    for group in ("horizon_comparisons", "individual_cases"):
        subset = [card for scope, card in supplementary if scope.startswith(group)]
        if subset:
            body.append(f"<details><summary>{group.replace('_', ' ').title()} — {len(subset)} views</summary>" + "".join(subset) + "</details>")
    document = '<!doctype html><html><head><meta charset="utf-8"><title>MEM final publication review</title><style>body{font:16px Aptos,"DejaVu Sans",sans-serif;margin:2rem auto;max-width:1350px;background:white;color:#222}img{width:100%;height:auto}article{margin:2rem 0 3rem}a{color:#245b8a}p{max-width:1100px}nav{position:sticky;top:0;background:#fff;padding:.6rem}</style></head><body><h1>MEM final publication review</h1><p>Current completeness: <strong>' + current["status"] + '</strong></p><p>2040 final_v3 · 2050 final_v6 · saved 2019 weather chronology. Original result authority, internal statuses and technical galleries remain preserved.</p><p>2040 final_v2 was prepared but not executed; evolution compares observed v1 and v3 only. Publication copies await editorial review.</p><nav><a href="PUBLICATION_FIGURE_REGISTER.csv">Figure register</a> · <a href="zonal_atlas/index.html">Zonal technical atlas</a> · <a href="editorial_review/20261011/index.html">Editorial alternatives</a> · <a href="README.md">Reproduction</a> · <a href="PUBLICATION_RECEIPT.json">Completeness / QA</a></nav>' + "".join(body) + '</body></html>'
    (owner / "index.html").write_text(document, encoding="utf-8")
    return rows


def _build(cfg, mode, year=None, scenario=None, sources=None, refresh=True):
    owner, sources = cfg["_owner"], sources or _sources(cfg)
    owner.mkdir(parents=True, exist_ok=True)
    years = [int(year)] if mode in ("case", "horizon") else None
    available, missing = available_cases(cfg, sources, years)
    if mode == "case":
        selected = [c for c in available if c["year"] == int(year) and c["scenario"] == scenario]
        if len(selected) != 1:
            raise ValueError("PUBLICATION_CASE_NOT_COMPLETE")
        folder = owner / "individual_cases" / str(year) / scenario
    else:
        selected = available
        folder = owner / "horizon_comparisons" / str(year) if mode == "horizon" else owner / "combined"
        if missing:
            _json(folder / "PARTIAL_STATE.json", dict(schema=SCHEMA, status="PARTIAL_WAITING_FOR_FINAL_QA", missing=missing))
            _json(owner / "PUBLICATION_RECEIPT.json", check(cfg, sources))
            _index(cfg)
            return dict(status="PARTIAL_WAITING_FOR_FINAL_QA", missing=missing, generated=0)
    definitions = [d for d in cfg["figures"] if any(c["year"] in d["years"] for c in selected) and (mode != "case" or d["id"] not in ("1", "2"))]
    generated, reused, waiting = 0, 0, []
    for definition in definitions:
        cases = [c for c in selected if c["year"] in definition["years"]]
        history = historical_cases(cfg, sources, definition["years"][0]) if definition["id"] in ("1", "2") else None
        references = [c for c in available if c["scenario"] == "High" and c["year"] in definition["years"]]
        if definition["id"] == "6C" and {c["year"] for c in cases} - {c["year"] for c in references}:
            waiting.append(dict(figure_id="6C", status="WAITING_FOR_HIGH_STRESS_REFERENCE"))
            continue
        # Historical evolution is already one horizon: reuse its authoritative
        # rendered asset instead of duplicating the graphic in combined.
        target = owner / "horizon_comparisons" / str(definition["years"][0]) if definition["id"] in ("1", "2") else folder
        _, changed = _save(cfg, sources, target, definition, cases, history, references if definition["id"] == "6C" else None)
        generated += int(changed)
        reused += int(not changed)
    partial = folder / "PARTIAL_STATE.json"
    if partial.exists():
        partial.unlink()
    _json(folder / "SCOPE_STATE.json", dict(schema=SCHEMA, status="COMPLETE" if not waiting else "PARTIAL", waiting=waiting,
        cases=[dict(year=c["year"], scenario=c["scenario"], variant=c["variant"]) for c in selected]))
    result = dict(status="COMPLETE" if not waiting else "PARTIAL", scope=mode, generated=generated, reused=reused,
        waiting=waiting, solver_invocations=0, network_opens=0, network_identities=sources.networks)
    if refresh:
        _json(owner / "LAST_RENDER_RECEIPT.json", result)
        _json(owner / "PUBLICATION_RECEIPT.json", check(cfg, sources))
        _index(cfg)
    return result


def build(mode="combined", year=None, scenario=None, config=CONFIG):
    cfg = configuration(config)
    with reporting_guard() as (network, solver):
        sources = _sources(cfg)
        available, missing = available_cases(cfg, sources)
        scopes = []
        if mode == "combined" and not missing:
            for case in available:
                scopes.append(_build(cfg, "case", case["year"], case["scenario"], sources, refresh=False))
            for horizon in cfg["baselines"]:
                scopes.append(_build(cfg, "horizon", horizon, sources=sources, refresh=False))
        if mode == "atlas":
            result = _build_atlas(cfg, sources, year)
        else:
            result = _build(cfg, mode, year, scenario, sources)
            if mode == "combined" and not missing and cfg.get("atlas", {}).get("enabled"):
                atlas = _build_atlas(cfg, sources)
                result["atlas"] = atlas
                result["generated"] += atlas["generated"]
                result["reused"] += atlas["reused"]
        if scopes:
            result["dependent_scopes"] = scopes
            result["generated"] += sum(item["generated"] for item in scopes)
            result["reused"] += sum(item["reused"] for item in scopes)
        changed = []
        for path, digest in sources.hashes.items():
            if path.is_relative_to(cfg["_owner"]):
                continue
            if sha256_file(path) != digest:
                changed.append(_identity(path))
        for name, identity in sources.networks.items():
            if identity.get("distribution") == "RECEIPT_IDENTITY_ONLY":
                continue
            stat = _path(name).stat()
            if (stat.st_size, stat.st_mtime_ns) != (identity["bytes"], identity["mtime_ns"]):
                changed.append(name)
        if changed:
            raise RuntimeError("PUBLICATION_PROTECTED_SOURCE_MUTATION: " + str(changed))
        result["protected_consumed_sources_unchanged"] = True
        _json(cfg["_owner"] / "PUBLICATION_RECEIPT.json", check(cfg, sources))
        _index(cfg)
        _json(cfg["_owner"] / "LAST_RENDER_RECEIPT.json", result)
        if network["network_opens"] or solver["solver_invocations"]:
            raise RuntimeError("PUBLICATION_EXECUTION_BOUNDARY_FAIL")
    return result


def _sources(cfg):
    selected = cfg.get("results_authority")
    if not selected:
        return Sources()
    path = _path(selected["path"])
    if sha256_file(path) != selected["sha256"]:
        raise ValueError("PUBLIC_RESULTS_AUTHORITY_HASH_MISMATCH")
    contract = _read(path)
    if contract["schema"] != "PYPSA_IT_PUBLIC_RESULTS_AUTHORITY_V1":
        raise ValueError("PUBLIC_RESULTS_AUTHORITY_SCHEMA")
    sources = Sources(contract)
    sources.pin(path, selected["sha256"])
    return sources


def selected_figures(config=CONFIG):
    """Render the eleven principal figures using saved, hash-checked tables."""
    cfg = configuration(config)
    with reporting_guard() as (network, solver):
        sources = _sources(cfg)
        result = _build(cfg, "combined", sources=sources, refresh=False)
        if result["status"] != "COMPLETE":
            raise ValueError("SELECTED_FIGURES_INCOMPLETE")
        for path, digest in sources.hashes.items():
            if not path.is_relative_to(cfg["_owner"]) and sha256_file(path) != digest:
                raise ValueError("PUBLIC_RESULTS_SOURCE_MUTATION")
        if network["network_opens"] or solver["solver_invocations"]:
            raise ValueError("PUBLIC_RESULTS_EXECUTION_BOUNDARY")
        result.update(selected_figure_types=11, source_data_unchanged=True)
        _json(cfg["_owner"] / "SELECTED_FIGURES_RECEIPT.json", result)
        return result


def _atlas_def(view, zone):
    return dict(id=f"ATLAS:{view}:{zone}", name=f"{zone.lower()}_{view}",
        purpose=f"{zone} zonal price " + {"monthly": "distribution by month", "duration": "duration across scenarios", "comparison_heatmap": "hourly chronology on a shared full-range scale"}[view],
        producer="mem_model.visualization.publication_price_atlas.render",
        qa="Same-run LP zonal prices; UC gross electrical-load weights and physical duration; full 8760 observations and extrema")


def _geography_def(view):
    return dict(id="GEOGRAPHY:" + view, name="geographic_" + view,
        purpose="Detailed MEM geographic " + {"price": "annual zonal prices", "flow": "signed UC net-interface flows", "utilization": "hours >=90% of directional capacity"}[view],
        producer="mem_model.visualization.publication_geography.render",
        qa="Seven Italian + eight external + CORS display nodes; twenty canonical interfaces; zero reference AC branches; saved stress selection; exact and >=90% metrics distinct")


def _atlas_expected(cfg):
    if cfg.get("atlas", {}).get("enabled"):
        for year in cfg["baselines"]:
            for zone in cfg["atlas"]["zones"]:
                for view in cfg["atlas"]["views"]:
                    yield f"zonal_atlas/{year}/{zone}", _atlas_def(view, zone)
            for view in cfg["atlas"].get("geographic_views", []):
                yield f"zonal_atlas/{year}/geography", _geography_def(view)


def _build_atlas(cfg, sources, year=None):
    """Use the same owner, source authority and hash cache; no new selection system."""
    from ..visualization.publication_price_atlas import existing_heatmap_options
    available, missing = available_cases(cfg, sources, [year] if year else None)
    generated = reused = 0
    waiting = []
    for horizon in cfg["baselines"]:
        if year and int(horizon) != int(year):
            continue
        cases = [c for c in available if c["year"] == horizon]
        if len(cases) != len(cfg["scenarios"]):
            waiting.append(dict(year=horizon, status="WAITING_FOR_THREE_VERIFIED_SCENARIOS"))
            continue
        for zone in cfg["atlas"]["zones"]:
            for view in cfg["atlas"]["views"]:
                _, changed = _save(cfg, sources, cfg["_owner"] / "zonal_atlas" / str(horizon) / zone,
                    _atlas_def(view, zone), cases)
                generated += int(changed)
                reused += int(not changed)
        for view in cfg["atlas"].get("geographic_views", []):
            _, changed = _save(cfg, sources, cfg["_owner"] / "zonal_atlas" / str(horizon) / "geography",
                _geography_def(view), cases)
            generated += int(changed)
            reused += int(not changed)
    references = []
    for row in existing_heatmap_options(available):
        paths = [*row["sources"], row["png"], row["svg"]]
        hashes = {_identity(path): sources.verify(path) for path in paths}
        references.append(dict(year=row["year"], scenario=row["scenario"], zone=row["zone"],
            view="existing_hourly_heatmap", scale=row["scale"], source_hashes=hashes,
            png=_identity(row["png"]), svg=_identity(row["svg"]), metadata=_identity(row["metadata"])))
    for case in available:
        for stem, view in (("MEM_ITALY_42BUS_ZONAL_PRICE", "geographic_price"),
                           ("MEM_ITALY_42BUS_INTERZONAL_FLOW", "geographic_flow"),
                           ("MEM_ITALY_42BUS_UTILIZATION_CONTEXT", "geographic_utilization")):
            folder = case["report"] / "VIS_X1/network"
            metadata = folder / (stem + ".metadata.json")
            value = _read(metadata)
            if value.get("spatial_scaffold") != "MEM_STAGE_B_7ZONE_EXTERNAL_GRAPH":
                raise ValueError("ATLAS_REFERENCE_GEOGRAPHY_IS_NOT_MEM_GRAPH")
            paths = [metadata, folder / (stem + ".png"), folder / (stem + ".svg")]
            hashes = {_identity(path): sources.verify(path) for path in paths}
            references.append(dict(year=case["year"], scenario=case["scenario"], zone="ALL",
                view=view, source_hashes=hashes, png=_identity(paths[1]), svg=_identity(paths[2]),
                metadata=_identity(metadata), spatial_scaffold=value["spatial_scaffold"],
                note="Legacy filename only; actual MEM 16-node / 20-interface graph, zero reference AC branches"))
    folder = cfg["_owner"] / "zonal_atlas"
    _json(folder / "SOURCE_REFERENCES.json", dict(schema=SCHEMA, references=references,
        original_assets_preserved=True, common_heatmap_scale_selection_pending=False,
        editorial_selection=cfg["presentation"]["editorial_selection"]))
    _atlas_index(cfg)
    result = dict(status="COMPLETE" if not missing and not waiting else "PARTIAL", generated=generated,
        reused=reused, references=len(references), waiting=waiting, solver_invocations=0, network_opens=0)
    _json(folder / "ATLAS_RECEIPT.json", result)
    return result


def _relative_url(path, folder):
    return html.escape(Path(os.path.relpath(_path(path), folder)).as_posix(), quote=True)


def _atlas_index(cfg):
    folder = cfg["_owner"] / "zonal_atlas"
    references = _read(folder / "SOURCE_REFERENCES.json")["references"]
    rows, blocks = [], []
    for year in cfg["baselines"]:
        section = [f'<h2 id="y{year}">{year}</h2>']
        for zone in cfg["atlas"]["zones"]:
            section.append(f'<details id="y{year}_{zone}"><summary>{zone} — monthly distribution, duration, hourly heatmaps</summary>')
            for view in cfg["atlas"]["views"]:
                definition = _atlas_def(view, zone)
                metadata = folder / str(year) / zone / (definition["name"] + ".json")
                if not metadata.exists():
                    section.append(f'<p>{view}: waiting for three verified scenarios</p>')
                    continue
                value = _read(metadata)
                image = metadata.with_suffix(".png"); svg = metadata.with_suffix(".svg"); data = metadata.with_suffix(".csv")
                section.append(f'<article><h3>{html.escape(value["analytical_purpose"])}</h3><a href="{_relative_url(image, folder)}"><img loading="lazy" src="{_relative_url(image, folder)}"></a><p>{html.escape(value["caption"])}</p><p><a href="{_relative_url(svg, folder)}">SVG</a> · <a href="{_relative_url(data, folder)}">CSV</a> · <a href="{_relative_url(metadata, folder)}">QA/provenance</a></p></article>')
                rows.append(dict(year=year, zone=zone, view=view, surface="ZONAL_TECHNICAL_ATLAS", producer=value["producer"],
                    png=_identity(image), svg=_identity(svg), data=_identity(data), metadata=_identity(metadata)))
            section.append('<h3>Preserved original hourly price heatmaps</h3><p>Independent single-scenario scales retain full extrema. Compare chronology; colours alone are not cross-scenario price comparisons. Use the coordinated atlas panel above for a common price scale.</p>')
            for reference in references:
                if reference["year"] == year and reference["zone"] == zone and reference["view"] == "existing_hourly_heatmap":
                    section.append(f'<details><summary>{reference["scenario"]}</summary><a href="{_relative_url(reference["png"], folder)}"><img loading="lazy" src="{_relative_url(reference["png"], folder)}"></a><p><a href="{_relative_url(reference["svg"], folder)}">SVG</a> · <a href="{_relative_url(reference["metadata"], folder)}">Original QA</a></p></details>')
                    rows.append({k:v for k,v in reference.items() if k != "source_hashes"})
            section.append('</details>')
        section.append('<details><summary>Coordinated geographic MEM views — Slow / Base / High</summary><p>Canonical MEM graph only; market display anchors are not substations or surveyed cable routes. Price and utilization scales are common within each horizon. Each flow case retains its own accepted stress hour; this is not a matched-hour comparison.</p>')
        for view in cfg["atlas"].get("geographic_views", []):
            definition = _geography_def(view)
            metadata = folder / str(year) / "geography" / (definition["name"] + ".json")
            if not metadata.exists():
                section.append(f'<p>{view}: waiting for three verified scenarios</p>')
                continue
            value = _read(metadata)
            image, svg, data = [metadata.with_suffix("." + ext) for ext in ("png", "svg", "csv")]
            section.append(f'<article><h3>{html.escape(value["analytical_purpose"])}</h3><a href="{_relative_url(image, folder)}"><img loading="lazy" src="{_relative_url(image, folder)}"></a><p>{html.escape(value["caption"])}</p><p><a href="{_relative_url(svg, folder)}">SVG</a> · <a href="{_relative_url(data, folder)}">CSV</a> · <a href="{_relative_url(metadata, folder)}">QA/provenance</a></p></article>')
            rows.append(dict(year=year, zone="ALL", view=view, surface="ZONAL_TECHNICAL_ATLAS", producer=value["producer"], png=_identity(image), svg=_identity(svg), data=_identity(data), metadata=_identity(metadata)))
        section.append('</details>')
        section.append('<details><summary>Existing geographic MEM views — prices / signed UC flow / ≥90% utilization</summary><p>These are the simulated seven-zone MEM graph. The separate 42-bus physical-reference topology is not a MEM result scaffold. Utilization ≥90% and exact capacity saturation are different measures; neither alone establishes physical AC congestion.</p>')
        for reference in references:
            if reference["year"] == year and reference["zone"] == "ALL":
                section.append(f'<details><summary>{reference["scenario"]} · {reference["view"]}</summary><a href="{_relative_url(reference["png"], folder)}"><img loading="lazy" src="{_relative_url(reference["png"], folder)}"></a><p><a href="{_relative_url(reference["svg"], folder)}">SVG</a> · <a href="{_relative_url(reference["metadata"], folder)}">Source authority</a></p></details>')
                rows.append({k:v for k,v in reference.items() if k != "source_hashes"})
        section.append('</details>')
        blocks.extend(section)
    pd.DataFrame(rows).to_csv(folder / "ATLAS_REGISTER.csv", index=False)
    page = '<!doctype html><html><head><meta charset="utf-8"><title>MEM zonal price atlas</title><style>body{font:16px Aptos,"DejaVu Sans",sans-serif;max-width:1300px;margin:2rem auto;color:#263442;background:white}img{width:100%;height:auto}details{margin:1.2rem 0}summary{cursor:pointer;font-weight:bold}article{margin:2rem 0}a{color:#245b8a}</style></head><body><h1>MEM zonal technical atlas</h1><p>2040 final_v3 · 2050 final_v6 · 2019 weather chronology · fixed-commitment LP prices; UC rigid load + P2X quantity/duration weighting. Technical detail does not enlarge the eleven-figure principal publication shortlist.</p><nav><a href="../index.html">Principal publication set</a> · <a href="#y2040">2040</a> · <a href="#y2050">2050</a> · <a href="ATLAS_REGISTER.csv">Atlas register</a> · <a href="SOURCE_REFERENCES.json">Preserved source references</a></nav>' + ''.join(blocks) + '</body></html>'
    (folder / "index.html").write_text(page, encoding="utf-8")


def _expected(cfg):
    for year in cfg["baselines"]:
        for scenario in cfg["scenarios"]:
            for d in cfg["figures"]:
                if d["id"] not in ("1", "2") and year in d["years"]:
                    yield (f"individual_cases/{year}/{scenario}", d)
        for d in cfg["figures"]:
            if year in d["years"]:
                yield (f"horizon_comparisons/{year}", d)
    for d in cfg["figures"]:
        if d["id"] not in ("1", "2"):
            yield ("combined", d)


def check(cfg=None, sources=None):
    """Read-only completeness and staleness check; never regenerate or write."""
    cfg = cfg or configuration()
    owner, sources = cfg["_owner"], sources or _sources(cfg)
    available, missing = available_cases(cfg, sources)
    code, environment, entries, failures = _code_pins(cfg), _environment(), [], []
    atlas_entries = []
    expected = [(scope, definition, False) for scope, definition in _expected(cfg)]
    expected += [(scope, definition, True) for scope, definition in _atlas_expected(cfg)]
    for scope, definition, is_atlas in expected:
        folder = owner / scope
        path = folder / (definition["name"] + ".json")
        state = "MISSING"
        if path.exists():
            receipt = _read(path)
            state = "COMPLETE"
            if receipt.get("name") != definition["name"] or receipt.get("figure_id") != definition["id"]:
                state = "INVALID_FIGURE_IDENTITY"
            if receipt["code_hashes"] != code:
                state = "STALE_CODE_OR_CONFIG"
            if receipt.get("render_environment") != environment:
                state = "STALE_RENDER_ENVIRONMENT"
            for key, digest in receipt["source_hashes"].items():
                source = _path(key)
                if not source.exists() or sources.sha(source) != digest:
                    state = "STALE_SOURCE"
            for key, digest in receipt["artifact_hashes"].items():
                artifact = folder / key
                if not artifact.exists() or sources.sha(artifact) != digest:
                    state = "MISSING_OR_CHANGED_ARTIFACT"
        entry = dict(figure_id=definition["id"], name=definition["name"], scope=scope, status=state,
            metadata=_identity(path))
        (atlas_entries if is_atlas else entries).append(entry)
        if state != "COMPLETE":
            failures.append(entry)
    reference_path = owner / "zonal_atlas/SOURCE_REFERENCES.json"
    if cfg.get("atlas", {}).get("enabled"):
        if not reference_path.exists():
            failures.append(dict(scope="zonal_atlas", status="MISSING_SOURCE_REFERENCES"))
        else:
            for reference in _read(reference_path)["references"]:
                for key, digest in reference["source_hashes"].items():
                    path = _path(key)
                    if not path.exists() or sources.sha(path) != digest:
                        failures.append(dict(scope="zonal_atlas", status="STALE_REFERENCED_ORIGINAL", source=key))
    return dict(schema=SCHEMA, status="COMPLETE" if not missing and not failures else "INCOMPLETE",
        completion_gate="MEM_FINAL_PUBLICATION_VISUALIZATION_REFINED_FOR_APPROVAL" if not missing and not failures else "PUBLICATION_INCOMPLETE",
        baseline_cases=[dict(year=c["year"], scenario=c["scenario"], variant=c["variant"], **c["authority"]) for c in available],
        missing_cases=missing, figures=entries, failures=failures, publication_figure_types=11, expected_rendered_assets=len(entries),
        atlas_figures=atlas_entries, expected_atlas_assets=len(atlas_entries),
        editorial_selection=cfg.get("presentation", {}).get("editorial_selection", "INITIAL_REVIEW"),
        historical_exceptions=[dict(year=2040, variant="final_v2", status="PREPARED_NOT_EXECUTED", numerical_substitution=False)],
        network_identities=sources.networks, solver_invocations=0, network_opens=0,
        historical_qa_unchanged=True, canonical_model_data_unchanged=True)


def after_successful_qa(year, scenario, config=CONFIG):
    """Lightweight callback; each scope independently gates on saved final QA."""
    cfg = configuration(config)
    build("case", year, scenario, config)
    build("horizon", year, config=config)
    return build("combined", config=config)


def complete(year, scenario, variant, config=CONFIG):
    """Future ninth stage: original numerical QA PASS survives rendering failure."""
    from .. import stage_b_uc2 as uc2
    cfg = configuration(config)
    with uc2.execution_variant(variant):
        selected = ROOT / cfg["baselines"][int(year)]["result_root"] / str(year) / scenario
        if uc2.case_root(int(year), scenario).resolve() != selected.resolve():
            raise ValueError("PUBLICATION_COMPLETE_REQUIRES_SELECTED_BASELINE_NAMESPACE")
        numerical = uc2.final_qa(int(year), scenario)
    if numerical["status"] != "PASS":
        return dict(status="FINAL_QA_FAILED", numerical=numerical)
    try:
        publication = after_successful_qa(int(year), scenario, config)
        return dict(status="FINAL_QA_PASS", numerical=numerical, publication=publication)
    except Exception as exc:
        failure = dict(status="FINAL_QA_PASS_PUBLICATION_FAILED", year=int(year), scenario=scenario,
            variant=variant, numerical=numerical, publication_error=f"{type(exc).__name__}: {exc}")
        cfg = configuration(config)
        _json(cfg["_owner"] / "errors" / f"{year}_{scenario}.json", failure)
        _json(cfg["_owner"] / "PUBLICATION_RECEIPT.json", dict(schema=SCHEMA,
            status="INCOMPLETE", completion_gate="PUBLICATION_INCOMPLETE", publication_error=failure["publication_error"]))
        return failure


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("case", "horizon", "combined", "atlas", "check", "complete", "selected"))
    parser.add_argument("--year", type=int, choices=(2040, 2050))
    parser.add_argument("--scenario", choices=("Slow", "Base", "High"))
    parser.add_argument("--variant", help="Existing execution namespace, only for future complete (ninth stage)")
    parser.add_argument("--config", type=Path, default=CONFIG)
    args = parser.parse_args(argv)
    if args.command in ("case", "horizon", "complete") and args.year is None:
        parser.error("--year required")
    if args.command in ("case", "complete") and args.scenario is None:
        parser.error("--scenario required")
    if args.command == "complete" and args.variant is None:
        parser.error("--variant required for complete")
    if args.command == "selected":
        value = selected_figures(args.config)
    elif args.command == "check":
        value = check(configuration(args.config))
    elif args.command == "complete":
        value = complete(args.year, args.scenario, args.variant, args.config)
    else:
        value = build(args.command, args.year, args.scenario, args.config)
    print(json.dumps(value, indent=2, default=str))
    return 1 if value["status"] in ("INCOMPLETE", "FINAL_QA_FAILED", "FINAL_QA_PASS_PUBLICATION_FAILED") else 0


if __name__ == "__main__":
    raise SystemExit(main())

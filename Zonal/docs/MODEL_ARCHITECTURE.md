# Model and data architecture

`src/mem_model/stage_b_uc2.py` routes each explicit final case through `final_methodology_networks.py`; 2050 final_v6 uses the accepted external-cost successor implementation. Prepared network pairs and their pinned lineage are restored by the stdlib bootstrap. V6 retains six parent networks because its static preflight checks the exact permitted input delta; these are validation dependencies, not extra simulation cases.

Simulation writes into its version-specific `results/` directory. Canonical reporting preserves physical UC and fixed-LP price authorities. The release's independent numerical tables live under `data/research_results/`, avoiding accidental reuse as new-solve prerequisites. `scripts/validate_results.py` is solver-free. The selected-figure producer uses a repository-owned results authority contract, receipt hashes and accepted table hashes; no solved network file is needed.

The accepted optional plotting toolkit is repository-relative under `optional/vis_x1`; the selected programme uses its sealed style utilities and bundled Natural Earth geography. Raw GISCO/reference-grid resources are not required for the principal figures. Nodal remains a planned independent model family.

Raw research reconstruction and Stage-A price generation are historical/upstream workflows. The current prepared Stage-B inputs already embody accepted external boundary prices, temporal/resource profiles, W/H/N/C controls and P2X/UC construction. No hidden scratch directory, old ZIP or developer machine is required by the advertised three routes.

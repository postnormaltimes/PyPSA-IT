# Reproducibility guide

Run the model from Zonal/. Download the assets of the zonal-production-data-v1 release into ../.data-packages/zonal-production-data-v1. The sealed archives are production_path_a_public_v1.zip and production_path_b_v1.zip; bootstrap installs their contents relative to Zonal/.

Install Python 3.11.15 and the packages pinned by uv.lock. Use Gurobi 13.0.3 with a valid locally configured licence. Licence files and credentials are neither distributed nor tracked.

The minimum execution download is the Path-A data asset: prepared final UC/reference network pairs and hydro-reporting support. Path B adds accepted baseline and renewable/hydro inputs for deterministic reconstruction. Hashes, destinations and roles are defined by inputs/manifests/data_package.csv. Bootstrap verifies all bytes and refuses to replace mismatched canonical assets.

Run the installation check before model execution. The 2040 Base reference fingerprint provides a compact comparison of objectives, demand/P2X, generation, storage/hydro, commitment and price statistics. Its technical reference status does not replace analytical result review. All six final_v1 cases are authorized for explicit manual execution; historical 2050 results cannot substitute for the current model.

Core reporting covers dispatch, balances, prices, trade, storage/hydro, water values, commitment and annual comparisons. Lexicographic marginal-regime analysis requires matching validated roots; missing roots are reported as WAITING without discovery or solving. Optional presentation uses the sealed toolkit under optional/vis_x1 and separately supplied NUTS2 geography. Bootstrap that geography with scripts/bootstrap_presentation.ps1 only when rendering is required.

Lightweight research evidence is retained in research. Larger source material and immutable historical evidence are separately preserved with hashes and source identity. Research reconstruction is distinct from the two executable model paths. Refer to the source register and methodological progression when modifying assumptions.

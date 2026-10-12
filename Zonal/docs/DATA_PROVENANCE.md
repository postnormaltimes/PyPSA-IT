# Data provenance

Canonical source registers and lightweight extracted contracts remain under `pre_pypsa_inputs/` and `inputs/`. Accepted national/zonal assumptions derive from the maintained MEM evidence, including Terna/PNIEC/DDS lineage and relevant national-source evidence. The prepared network inputs freeze the final scientific transformations rather than requiring raw research workbooks at execution time.

The final source-to-release chain is: accepted static/temporal/runtime and external-price authorities → accepted P2X and final W/H/N/C transformations → prepared reference/UC pairs → verified reference/UC/fixed-price solves → canonical physical/price reporting → public numerical exports and selected figures.

`inputs/manifests/data_package.csv` records prepared inputs, original/public SHA-256, destination, authority and transformation. The public derivatives alter only portable NetCDF metadata and dependent hash references. Stored numerical variables, coordinates, dimensions and non-whitelisted attributes are identical. V6 parent references are rebased to the corresponding public parent hashes.

`inputs/manifests/results_package.csv` records each result dataset, original saved source hash, exported hash, precision/format transformation and case/authority. Numerical Parquet/CSV sources remain byte-identical unless explicitly exported to CSV. Receipt derivatives use relative public paths and translated metadata hashes; original identities remain in the manifest. Public result provenance does not include machine names, private paths or solver credentials.

Historical evolution tables are retained only where used by accepted figures, clearly outside final six-case execution authority. The 2040 Base accepted same-LP marginal supplement is included as a canonical public evidence dependency. Large raw LP/KKT arrays and solved networks are not required to redraw the selected figures; further raw diagnostic reconstruction is not claimed.

No raw PDFs, private workbooks, confidential material or licence files are included. The software MIT licence does not change third-party data terms. See repository [third-party notices](../../THIRD_PARTY_NOTICES.md).

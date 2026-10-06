# Data provenance

See [sources](releases/SOURCES.md), `qa/releases/final_v4/SOURCE_REGISTER.csv`
and `inputs/manifests/` for source references and prepared-input hashes.

Portable NetCDF publication copies differ from local execution inputs only
in `meta.runtime_bundle`: the original machine directory is replaced by the
same project-relative runtime path. Every model variable, coordinate,
time series and other metadata field is identical. Original and publication
hashes are both retained in `PUBLICATION_NETWORK_IDENTITY.csv`.

Public technical records contain the fields needed to verify the distributed
inputs. Private historical receipts, source files and console transcripts are
not included. Original solved-result hashes and execution versions are retained
in `CANONICAL_RESULT_REGISTRY.csv`; they are not hashes of new public exports.

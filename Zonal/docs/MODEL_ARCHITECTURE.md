# Model architecture

`src/mem_model/` contains input transformations, execution, verification,
reporting and visualisation. `config/` defines variants and scenario controls;
`pre_pypsa_inputs/` contains lightweight model contracts. Prepared networks and
larger runtime members are restored from the Zonal data release, not tracked
in Git. `qa/` retains selected technical manifests and checks.

`python -m mem_model.stage_b_uc2` is the shared manual execution interface.
Each invocation performs one action for one case. Reference, UC and pricing
jobs have separate IDs and result directories. Hash, same-case dependency and
overwrite checks remain mandatory. Final_v4 does not duplicate the solver or
reporting architecture.

Historical solved binaries and original execution records remain in the
separate result store. Lightweight result registries record their hashes and
original versions without relabelling earlier solves.

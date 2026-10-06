# Italian seven-zone electricity model

The Zonal model represents NORD, CNOR, CSUD, SUD, CALA, SICI and SARD with
8,760 hourly snapshots. Slow, Base and High cases are defined for 2040 and
2050. Capacities and annual demand are fixed scenario inputs; the model does
not optimise investment.

The current configuration is **final_v4**. The 2040 configuration is unchanged
from final_v3, and the original solved 2040 Base result remains the reference
result with its original execution provenance. The 2050 configuration adopts
Terna's later solar, wind and nuclear capacity assumptions. Its Base case has
been solved and validated; Slow and High inputs are defined and manually
executable. The earlier 2050 Base configuration is retained as the PNIEC-capacity
benchmark.

Physical dispatch comes from a full-year unit-commitment MILP. Prices and water
values come from a separate fixed-commitment LP. A continuous reference LP
provides a comparison without unit commitment. Runs are explicit, single-case
commands: completing or reporting one case never launches another.

## Documentation

- [Methodology](docs/METHODOLOGY.md) and [model architecture](docs/MODEL_ARCHITECTURE.md)
- [Scenario definitions](docs/SCENARIO_GOVERNANCE.md)
- [Capacity revision](docs/releases/FINAL_V4.md) and [validation and limitations](docs/releases/VALIDATION.md)
- [Reproduction and manual execution](docs/releases/REPRODUCIBILITY.md)
- [Source provenance](docs/releases/SOURCES.md)

Prepared inputs are distributed separately in the
[zonal-production-data-v2 release](https://github.com/postnormaltimes/PyPSA-IT/releases/tag/zonal-production-data-v2).
The original `zonal-production-data-v1` release remains available for the
earlier configuration. Solved network binaries and private local evidence are
not stored in Git.

Software is [MIT-licensed](../LICENSE). Third-party source terms are described
in [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md).

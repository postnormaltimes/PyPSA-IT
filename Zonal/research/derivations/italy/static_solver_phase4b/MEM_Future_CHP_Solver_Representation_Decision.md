# MEM future CHP solver representation — Phase 4B

## Controlling decision

The primary MEM solver uses **electricity-only CHP-specific dispatch sub-bands**. Combined CHP + non-CHP capacity remains the frozen geography anchor, but applicable parent capacity is split inside each market zone into mutually exclusive CHP and non-CHP generators. No capacity moves between zones.

No heat buses, heat demand, heat credit or automatic minimum-output constraint are created. Historical CHP capacity shares are allocation proxies, and historical capacity factors are characterization evidence only. CCGT/GT/engine CHP variants inherit the same electricity-side conversion efficiency as the corresponding non-CHP conversion where the technical source does not justify a separate number. Bioenergy CHP uses a distinct 0.3003 electricity efficiency versus 0.468 for the non-CHP proxy.

The later **CHP_HEAT_LED_MUST_RUN_SENSITIVITY** is mandatory. It may introduce an externally specified heat-led minimum or availability profile, but it must not overwrite the primary electricity-only case.

# 2050 Base validation and limitations

The continuous reference, unit-commitment MILP and fixed-commitment price LP
completed, and their verification and reporting checks passed. No material
implementation defect was found in the capacity revision. Batteries and zonal
allocation keys were preserved, and programmable capacity reconciles exactly.

Compared with the prior PNIEC-capacity benchmark, physical UC load shedding
and VRE curtailment are materially lower. Remaining shedding is very small
and concentrated in northern zones. It is a spatial adequacy signal rather
than evidence requiring national-capacity recalibration.

Nuclear generation is approximately 68.4 TWh, or 78% utilisation, close to
Terna's approximately 69 TWh / 80% analytical case. Combined solar, wind and
hydro generation is also close to the later Terna benchmark. Differences in
imports and other programmable generation reflect model scope and assumptions
held fixed in this revision. Terna's analysis is itself not presented as a
closed, full-system optimisation.

Physical generation, shedding, commitment and storage operation are taken from
the verified UC MILP, not from the pricing LP. Zonal prices and native Store
water values are taken from the verified fixed-commitment LP.

# MEM future CHP representation decision

## Controlling decision

FUTURE_PRIMARY_CHP_REPRESENTATION = AGGREGATED_ELECTRICITY_ONLY.

Future CCGT and GT/OCGT capacities use combined historical CHP and non-CHP geography but are instantiated as aggregated electricity-only conversion/fuel bands in the primary MEM model. Historical CHP capacity, electricity generation, produced heat, electric capacity factor and electricity-to-heat ratios remain calibration metadata.

The primary model does not include a coupled heat network. A later CHP_HEAT_LED_SENSITIVITY remains mandatory after the base model operates, testing a must-run or heat-led availability/output treatment without changing the historical evidence layer.

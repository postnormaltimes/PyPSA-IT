from __future__ import annotations

import pytest

from mem_model.common import assert_gate


@pytest.mark.parametrize("gate", ["chronology", "external_prices", "runtime_assumptions"])
def test_unapproved_hourly_gates_fail_closed(gate: str) -> None:
    with pytest.raises(RuntimeError, match="not approved"):
        assert_gate(gate)

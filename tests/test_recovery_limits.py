import time

import pytest

from continuum.recovery import RecoveryTimeoutError, run_with_limits


def test_runaway_op_is_terminated_not_hung() -> None:
    def slow_op() -> str:
        time.sleep(0.5)
        return "done"

    with pytest.raises(RecoveryTimeoutError):
        run_with_limits(slow_op, timeout=0.05)


def test_limits_opt_in_no_timeout_runs_normally() -> None:
    def fast_op(x: int) -> int:
        return x * 2

    assert run_with_limits(fast_op, 21) == 42
    assert run_with_limits(fast_op, 21, timeout=None) == 42


def test_invalid_timeout_rejected() -> None:
    with pytest.raises(ValueError):
        run_with_limits(lambda: None, timeout=0)

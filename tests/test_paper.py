from robo.paper import Paper
from robo.store import Store
from test_scanner import quote
import pytest


def test_paper_two_legs_costs_close_idempotence_and_kill_switch(tmp_path):
    s = Store(tmp_path)
    p = Paper(s)
    q = quote(rate=0.009)
    pos = p.open(q, 100, 100000000, "one", False, "normal")
    assert pos["status"] == "OPEN"
    assert p.open(q, 100, 100000000, "one", False, "normal")["id"] == pos["id"]
    assert len(p.state()["positions"]) == 1
    out = p.close(pos["id"], q, 100000000)
    assert out["status"] == "CLOSED" and out["realized_pnl"] < 0
    assert p.state()["reserved"] == 0
    assert p.state()["balance"] == pytest.approx(1000 + out["realized_pnl"])
    assert p.close(pos["id"], q, 100000000) == out
    p.halt(True)
    with pytest.raises(ValueError, match="HALTED"):
        p.open(q, 100, 100000000, "two", False, "normal")


def test_partial_second_leg_failure_is_compensated_and_funding_not_faked(tmp_path):
    p = Paper(Store(tmp_path))
    q = quote(rate=0.009)
    pos = p.open(q, 100, 100000000, "f", False, "second_leg_failure")
    assert pos["status"] == "FAILED_HEDGED" and pos["realized_pnl"] < 0
    assert p.state()["reserved"] == 0
    pos = p.open(q, 100, 100000000, "p", False, "partial")
    assert pos["quantity"] < 1 and pos["status"] == "OPEN"
    # Unknown mark at settlement: explicit pending, never use current/future mark.
    p.settle(pos["id"], [{"ts": 103600000, "rate": 0.001}], [], 103600001)
    assert p.state()["positions"][-1]["pending_funding"] == [103600000]
    mark = quote(now=103600000)
    p.settle(pos["id"], [{"ts": 103600000, "rate": -0.001}], [mark], 103600001)
    p.settle(pos["id"], [{"ts": 103600000, "rate": -0.001}], [mark], 103600002)
    assert p.state()["positions"][-1]["funding_pnl"] == pytest.approx(-0.051)

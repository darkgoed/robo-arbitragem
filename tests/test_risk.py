"""Synthetic deterministic stress fixtures; never loaded by the production collector."""

import pytest
from robo.engine import scan, forecast_funding
from robo.paper import Paper
from robo.store import Store
from test_scanner import quote


@pytest.mark.parametrize(
    "field,value,reason",
    [
        ("funding_rate", 0.02, "INVALID_FUNDING"),
        ("interval_h", 0, "INVALID_SCHEDULE"),
        ("next_funding", 0, "INVALID_SCHEDULE"),
        ("mark", 0, "INVALID_MARK"),
        ("spot_fee", float("nan"), "INVALID_FEE"),
    ],
)
def test_bad_public_values_fail_closed(field, value, reason):
    q = quote()
    q[field] = value
    assert reason in scan(q, 100, 100000000)["reasons"]


def test_spread_fees_depth_drift_and_schedule_count():
    q = quote()
    r = scan(q, 100, 100000000)
    assert r["costs"]["fees_roundtrip"] == pytest.approx(0.3224)
    assert r["costs"]["spread_depth_roundtrip"] == pytest.approx(0.04)
    assert r["costs"]["price_latency_buffer"] == pytest.approx(0.20002)
    assert r["costs"]["rebalance_fx_buffer"] == pytest.approx(0.10001)
    q["perp"]["asks"] = [[102.01, 0.01]]
    assert "INSUFFICIENT_DEPTH" in scan(q, 100, 100000000)["reasons"]
    q = quote()
    q["mark"] = 110
    assert "PRICE_DRIFT" in scan(q, 100, 100000000)["reasons"]
    q = quote()
    q["spot"]["bids"] = [[99, 1], [100, 1]]
    assert "INVALID_BOOK_ORDER" in scan(q, 100, 100000000)["reasons"]
    assert forecast_funding(1000, 0.001, 4, 3600000, 0, 8) == 1
    assert forecast_funding(1000, 0.001, 1, 3600000, 0, 8) == 4


def test_paper_margin_buffer_and_missing_settlement_are_not_hidden(tmp_path):
    p = Paper(Store(tmp_path))
    q = quote(rate=0.009)
    pos = p.open(q, 100, 100000000, "margin")
    p.settle(pos["id"], [], [], 103600001)
    assert p.state()["positions"][0]["pending_funding"] == [103600000]
    shock = quote()
    shock["mark"] = 200
    shock["perp"]["bids"] = [[199.99, 10]]
    shock["perp"]["asks"] = [[200.01, 10]]
    p.monitor({"bybit:BTCUSDT": shock}, 100000000)
    assert p.state()["halted"]
    assert p.state()["positions"][0]["close_reason"] == "margin_buffer_or_stop"


def test_unhedged_residual_is_not_reported_as_flat(tmp_path):
    p = Paper(Store(tmp_path))
    q = quote(rate=0.009)
    pos = p.open(q, 100, 100000000, "residual", False, "compensation_failure")
    assert pos["status"] == "RESIDUAL_SPOT" and p.state()["halted"]
    assert pos["short_quantity"] == 0 and p.state()["reserved"] > 0
    p.settle(pos["id"], [{"ts": 103600000, "rate": 0.01}], [quote(103600000)], 103600001)
    assert p.state()["positions"][0]["funding_pnl"] == 0
    assert p.close(pos["id"], q, 100000000)["status"] == "CLOSED"
    assert p.state()["reserved"] == 0


def test_idempotency_key_different_payload_conflicts(tmp_path):
    p = Paper(Store(tmp_path))
    q = quote(rate=0.009)
    p.open(q, 100, 100000000, "idempotency")
    with pytest.raises(ValueError, match="IDEMPOTENCY_CONFLICT"):
        p.open(q, 200, 100000000, "idempotency")

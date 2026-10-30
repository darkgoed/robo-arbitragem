from robo.engine import scan, common_quantity, vwap
import pytest


def quote(now=100000000, rate=0.0001):
    def book(price):
        return {"bids": [[price - 0.01, 10]], "asks": [[price + 0.01, 10]], "ts": now}

    rule = {"step": 0.001, "min_qty": 0.001, "min_notional": 5, "max_qty": 100, "tick": 0.1, "active": True}
    return {
        "venue": "bybit",
        "symbol": "BTCUSDT",
        "collected_at": now,
        "metadata_at": now,
        "spot": book(100),
        "perp": book(102),
        "spot_rule": rule,
        "perp_rule": rule,
        "funding_rate": rate,
        "interval_h": 8,
        "next_funding": now + 3600000,
        "ticker_at": now,
        "mark": 102,
        "index": 100,
        "spot_fee": 0.001,
        "perp_fee": 0.0006,
    }


def test_roundtrip_not_fake_basis_profit_and_tiny_capital_rejected():
    q = quote()
    r = scan(q, 100, 100000000)
    assert r["basis_pct"] == pytest.approx(2)
    assert r["net_usdt"] < 0
    assert not r["eligible"] and "NET_NONPOSITIVE" in r["reasons"]
    assert scan(q, 0.3, 100000000)["eligible"] is False
    assert "MIN_NOTIONAL" in scan(q, 0.3, 100000000)["reasons"]
    assert common_quantity(0.0089, [0.003, 0.002]) == 0.006
    assert vwap([[100, 1], [102, 1]], 2) == 101
    with pytest.raises(ValueError):
        vwap([[100, 1]], 2)


def test_stale_invalid_clock_outlier_and_negative_funding_are_fail_closed():
    q = quote(rate=-0.001)
    assert "FUNDING_NONPOSITIVE" in scan(q, 100, 100000000)["reasons"]
    assert "STALE_BOOK" in scan(q, 100, 100020000)["reasons"]
    assert "FUTURE_CLOCK" in scan(q, 100, 99997000)["reasons"]
    q["funding_rate"] = float("nan")
    assert "INVALID_FUNDING" in scan(q, 100, 100000000)["reasons"]

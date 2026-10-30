"""Synthetic causal replay, separate from all real market evidence."""

from robo.store import Store
from robo.backtest import run_research
from test_scanner import quote


def test_replay_uses_actual_future_settlements_not_forecast_profit(tmp_path):
    store = Store(tmp_path)
    start = 100000000
    for minute in range(1441):
        ts = start + minute * 60000
        q = quote(ts, rate=0.009)
        q["next_funding"] = start + ((minute // 480) * 8 + 4) * 3600000
        if q["next_funding"] <= ts:
            q["next_funding"] += 8 * 3600000
        store.save_snapshot(q)
    store.save_funding(
        "bybit", "BTCUSDT", [{"timestamp": start + h * 3600000, "fundingRate": -0.001} for h in [4, 12, 20]]
    )
    report = run_research(store, "bybit", "BTCUSDT", 100, version="synthetic-replay-test")
    assert report["status"] == "REPLAY_COMPLETED"
    assert len(report["trades"]) == 1
    assert report["trades"][0]["status"] == "CLOSED"
    assert report["trades"][0]["funding_pnl"] < 0
    assert report["paper_net_pnl"] < 0  # positive forecast did not overwrite actual negative funding
    assert report["ml"]["promoted"] is False and report["sharpe"] is None


def test_prospective_gap_blocks_replay(tmp_path):
    store = Store(tmp_path)
    for i in range(48):
        store.save_snapshot(quote(100000000 + i * 86400000 // 47))
    r = run_research(store, "bybit", "BTCUSDT", 100)
    assert r["status"] == "INSUFFICIENT_DATA" and r["gaps"] == 47

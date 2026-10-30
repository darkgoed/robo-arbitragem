from robo.backtest import run_research
from robo.store import Store
from test_scanner import quote


def test_no_data_and_hindsight_funding_are_not_called_profit(tmp_path):
    s = Store(tmp_path)
    r = run_research(s, "bybit", "BTCUSDT", 100, version="test")
    assert r["status"] == "INSUFFICIENT_DATA" and r["sharpe"] is None
    s.save_funding(
        "bybit",
        "BTCUSDT",
        [{"timestamp": 1000, "fundingRate": 0.001}, {"timestamp": 2000, "fundingRate": -0.001}],
    )
    s.save_snapshot(quote())
    r = run_research(s, "bybit", "BTCUSDT", 100, version="test")
    assert r["funding_study"]["n"] == 2
    assert r["funding_study"]["fixed_notional_funding_only"] == 0
    assert r["trades"] == [] and r["ml"]["promoted"] is False
    assert "not a PnL backtest" in r["funding_study"]["warning"]

from robo.monitor import Monitor
from robo.store import Store
from test_monitor import quotes
from test_minimum import fx


def test_positive_hypothesis_is_rejected_when_research_capital_is_insufficient(tmp_path):
    qs = quotes(100000000)
    q = qs["bybit:BTCUSDT"]
    q["funding_rate"] = 0.009
    q["spot_rule"]["min_qty"] = 8
    q["perp_rule"]["min_qty"] = 8
    run = Monitor(Store(tmp_path)).evaluate(qs, fx(), 100000000, "test")
    row = next(
        d for d in run["decisions"] if d["market"] == "bybit:BTCUSDT" and d["scenario"] == "REALISTIC_MINIMUM"
    )
    assert row["projection"]["net_usdt"] > 0
    assert row["decision"] == "REJECT"
    assert "INSUFFICIENT_RESEARCH_CAPITAL" in row["reasons"]
    assert row["research_available_usdt"] == 1000

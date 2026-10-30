import copy
from robo.store import Store
from robo.monitor import Monitor
from test_scanner import quote
from test_minimum import fx


def quotes(now) -> dict[str, dict]:
    return {
        f"{v}:{s}": dict(copy.deepcopy(quote(now)), venue=v, symbol=s)
        for v in ["bybit", "bitget"]
        for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    }


def test_autonomous_hypotheses_persist_rejections_and_baseline_without_fills(tmp_path):
    store = Store(tmp_path)
    m = Monitor(store)
    run = m.evaluate(quotes(100000000), fx(), 100000000, "test")
    assert len(run["decisions"]) == 18
    assert run["rejects"] == 18 and run["passes"] == 0
    assert run["brl5"]["balance"] == 5 and run["brl5"]["fills"] == 0
    assert run["brl5"]["win_rate"] is None
    assert run["auto_open"] is False and run["live"] is False
    assert m.evaluate(quotes(100000001), fx(), 100000001, "test")["window"] == run["window"]
    summary = Monitor(Store(tmp_path)).summary(100000002)
    assert summary["total_runs"] == 1 and summary["total_decisions"] == 18
    assert len(summary["latest"]["decisions"]) == 18
    assert store.get("paper") is None


def test_missing_data_is_counted_not_skipped_and_late_rows_not_eligible(tmp_path):
    m = Monitor(Store(tmp_path))
    m.evaluate({}, None, 100000000, "test")
    summary = m.summary(100400000)
    assert summary["total_decisions"] == 18
    assert summary["availability_pct"] == 0
    assert summary["latest_current"] is False
    assert all(r["decision"] == "REJECT" for r in summary["latest"]["decisions"])


def test_shadow_brl1000_uses_real_minimum_without_touching_paper(tmp_path):
    store = Store(tmp_path)
    run = Monitor(store).evaluate(quotes(100000000), fx(), 100000000, "test")
    shadow = run["shadow_brl1000"]
    assert shadow["balance"] == 1000 and shadow["fills"] == 0
    assert len(shadow["decisions"]) == 6
    assert all(d["execution"] == "NOT_ENABLED" and d["realized_pnl_brl"] == 0 for d in shadow["decisions"])
    assert all(d["capital_sufficient"] for d in shadow["decisions"])
    assert all(d["decision"] == "REJECT" and "NET_NONPOSITIVE" in d["reasons"] for d in shadow["decisions"])
    assert store.get("paper") is None
    assert len(run["decisions"]) == 18
    assert Monitor(Store(tmp_path)).detail(run["window"])["shadow_brl1000"]["balance"] == 1000


def test_shadow_brl1000_rejects_missing_market_data(tmp_path):
    run = Monitor(Store(tmp_path)).evaluate({}, None, 100000000, "test")
    assert len(run["shadow_brl1000"]["decisions"]) == 6
    assert run["shadow_brl1000"]["available_markets"] == 0
    assert all(d["decision"] == "REJECT" for d in run["shadow_brl1000"]["decisions"])

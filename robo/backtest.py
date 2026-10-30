"""Causal collected-book replay; no historical executable book is manufactured."""

import hashlib
import os
import tempfile
import uuid

from robo.engine import scan
from robo.paper import Paper
from robo.store import Store, encode, now_ms


def run_research(store, venue, symbol, notional: float = 100, version="unknown"):
    data = store.snapshots(venue, symbol, limit=5000)
    history = store.history(venue, symbol)
    digest = hashlib.sha256(encode({"snapshots": data, "funding": history}).encode()).hexdigest()
    rates = [r["rate"] for r in history]
    report = {
        "id": uuid.uuid4().hex,
        "created": now_ms(),
        "venue": venue,
        "symbol": symbol,
        "version": version,
        "seed": 0,
        "dataset_sha256": digest,
        "config": {
            "notional": notional,
            "strategy": "fixed_24h_cash_carry",
            "fees": "snapshot",
            "latency_adverse_bps": 5,
            "trial": len(store.experiments()) + 1,
        },
        "range": [data[0]["collected_at"], data[-1]["collected_at"]] if data else None,
        "snapshot_count": len(data),
        "status": "INSUFFICIENT_DATA",
        "trades": [],
        "baseline_no_trade_pnl": 0,
        "paper_net_pnl": None,
        "max_drawdown_usdt": None,
        "sharpe": None,
        "deflated_sharpe": None,
        "ml": {
            "challenger": "lightweight funding-sign reversal classifier",
            "trained": False,
            "promoted": False,
            "reason": "No purged walk-forward/OOS advantage established; no heavy training",
        },
        "funding_study": {
            "n": len(rates),
            "from": history[0]["ts"] if history else None,
            "to": history[-1]["ts"] if history else None,
            "fixed_notional_funding_only": sum(rates) * notional,
            "positive_events": sum(r > 0 for r in rates),
            "negative_events": sum(r < 0 for r in rates),
            "warning": "not a PnL backtest; no historical mark/book/fees; fixed notional, retrospective attribution",
        },
        "limitations": [
            "Public REST books are observations, not guaranteed fills",
            "No maker queue, exchange liquidation engine or historical order rejection model",
            "No independent OOS period, significance, DSR or ML edge claimed",
        ],
    }
    if len(data) < 48 or data[-1]["collected_at"] - data[0]["collected_at"] < 86400000:
        report["reason"] = (
            "Requires >=24h of prospective books and >=3 covered settlements; historical funding alone is insufficient"
        )
        store.experiment(report)
        return report
    gaps = sum(b["collected_at"] - a["collected_at"] > 90000 for a, b in zip(data, data[1:], strict=False))
    covered = [e for e in history if data[0]["collected_at"] < e["ts"] <= data[-1]["collected_at"]]
    if gaps or len(covered) < 3:
        report["reason"] = "Coverage gaps or fewer than 3 settlements"
        report["gaps"] = gaps
        store.experiment(report)
        return report
    # Reuse the exact paper ledger, not a second, inconsistent fill/PnL implementation.
    with tempfile.TemporaryDirectory(
        prefix="robo-backtest-", dir=os.environ.get("TMPDIR", str(store.directory))
    ) as td:
        sim = Paper(Store(td))
        peak, drawdown = 1000.0, 0.0
        previous_position = None
        for q in data:
            now = q["collected_at"]
            if previous_position:
                sim.settle(previous_position, history, data, now)
            opens = [p for p in sim.state()["positions"] if p["status"] == "OPEN"]
            if opens:
                p = opens[0]
                sim.monitor({venue + ":" + symbol: q}, now)
                if now - p["opened"] >= 86400000:
                    sim.close(p["id"], q, now, "24h_exit")
            elif not sim.state()["halted"] and scan(q, notional, now)["eligible"]:
                previous_position = sim.open(q, notional, now, str(now))["id"]
            balance = sim.state()["balance"]
            peak = max(peak, balance)
            drawdown = max(drawdown, peak - balance)
        states = sim.state()["positions"]
        closed = [p for p in states if p["status"] == "CLOSED"]
        pending = [p for p in states if p["status"] == "OPEN" or p["pending_funding"]]
        report.update(
            trades=states,
            paper_net_pnl=sum(p["realized_pnl"] for p in closed),
            max_drawdown_usdt=drawdown,
            status="INCOMPLETE_OPEN_EXPOSURE" if pending else "REPLAY_COMPLETED",
            reason="No eligible entries is a valid baseline result"
            if not states
            else "Collected-book replay only",
        )
    store.experiment(report)
    return report

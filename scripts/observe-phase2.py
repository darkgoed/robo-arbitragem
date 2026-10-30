"""Observe natural persisted decision windows; never forces or backdates a run."""

import gzip
import hashlib
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
deadline = time.monotonic() + 330
while True:
    with sqlite3.connect(ROOT / "data/research.sqlite3") as db:
        rows = db.execute("SELECT window,ts,payload,sha256 FROM monitor_runs ORDER BY window").fetchall()
    if len(rows) >= 2:
        break
    assert time.monotonic() < deadline, "second natural monitor window not observed"
    time.sleep(2)
runs = []
for window, ts, raw, digest in rows:
    assert hashlib.sha256(raw).hexdigest() == digest
    run = json.loads(gzip.decompress(raw))
    assert window == ts // 300000 and len(run["decisions"]) == 18
    assert run["brl5"] == {
        "initial": 5,
        "balance": 5,
        "realized_pnl": 0,
        "fills": 0,
        "win_rate": None,
        "baseline": "NO_TRADE",
    }
    assert not run["auto_open"] and not run["live"]
    runs.append(
        {
            "window": window,
            "asof": ts,
            "version": run["version"],
            "passes": run["passes"],
            "rejects": run["rejects"],
            "available": run["available"],
            "decisions": len(run["decisions"]),
            "sha256": digest,
        }
    )
assert len({r["window"] for r in runs}) == len(runs)
with sqlite3.connect(ROOT / "data/research.sqlite3") as db:
    ledger = json.loads(db.execute("SELECT body FROM state WHERE key='paper'").fetchone()[0])
    count = db.execute("SELECT COUNT(*),COUNT(DISTINCT window) FROM monitor_runs").fetchone()
assert count[0] == count[1]
report = {
    "status": "PASS",
    "at": time.time(),
    "natural_runs": runs,
    "total_decisions": sum(r["decisions"] for r in runs),
    "brl5_balance": 5,
    "brl5_realized_pnl": 0,
    "brl5_fills": 0,
    "paper_research_balance": ledger["balance"],
    "paper_positions": len(ledger["positions"]),
    "note": "Natural 5-minute windows, not simulated uptime or a profitability record.",
}
(ROOT / "evidence/phase2-natural-monitor.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))

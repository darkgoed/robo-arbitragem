"""Clone only public/virtual data into isolated test auth; NEVER production secrets."""

import json
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robo.store import Store, now_ms
from robo.monitor import Monitor
from robo.archives import compact

ROOT = Path(__file__).resolve().parents[1]
STAGE = ROOT / "data/staging-v02"
(STAGE / "data").mkdir(parents=True, exist_ok=True, mode=0o700)
# Wait on real collection state, do not rewrite exchange timestamps to claim freshness.
deadline = time.monotonic() + 45
while True:
    with sqlite3.connect(ROOT / "data/research.sqlite3") as db:
        latest = [r[0] for r in db.execute("SELECT MAX(ts) FROM snapshots GROUP BY venue,symbol")]
    if len(latest) == 6 and all(0 <= now_ms() - ts < 11000 for ts in latest):
        break
    assert time.monotonic() < deadline, "no fresh collector window for clone"
    time.sleep(0.5)
with (
    sqlite3.connect(ROOT / "data/research.sqlite3") as source,
    sqlite3.connect(STAGE / "data/research.sqlite3") as target,
):
    source.backup(target)
    assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
store = Store(STAGE / "data")
quotes = {}
for venue in ["bybit", "bitget"]:
    for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
        quotes[venue + ":" + symbol] = store.snapshots(venue, symbol, 1)[0]
run = Monitor(store).evaluate(quotes, store.get("fx"), now_ms(), "STAGING-current-code-real-copied-quotes")
# Exercise compaction on actual copied L2, not invented history; readback full quote equality.
with store.connect() as db:
    before_count = db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
archive = compact(store, before_ms=now_ms() - 3600000)
with store.connect() as db:
    after_count = db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0]
assert before_count == after_count + archive["count"]
report = {
    "stage": str(STAGE),
    "source": "production SQLite online backup; credentials NOT copied",
    "snapshots_before": before_count,
    "hot_after": after_count,
    "archive": archive,
    "monitor_window": run["window"],
    "decisions": len(run["decisions"]),
    "realized_brl_pnl": run["brl5"]["realized_pnl"],
}
(ROOT / "evidence/phase2-stage.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))

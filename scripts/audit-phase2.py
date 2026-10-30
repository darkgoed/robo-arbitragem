"""Read-only streaming audit of actual prospective data, no password plaintext."""

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robo.store import Store, now_ms
from robo.engine import quality

ROOT = Path(__file__).resolve().parents[1]
store = Store(ROOT / "data")
report = {
    "at": datetime.now(timezone.utc).isoformat(),
    "markets": [],
    "funding": [],
    "auth_fingerprint": hashlib.sha256((ROOT / "secrets/auth.json").read_bytes()).hexdigest(),
    "schema": [],
}
with store.connect() as db:
    report["integrity"] = db.execute("PRAGMA quick_check").fetchone()[0]
    report["schema"] = [r[0] for r in db.execute("SELECT version FROM migrations")]
    for venue, symbol, count, first, last in db.execute(
        "SELECT venue,symbol,COUNT(*),MIN(ts),MAX(ts) FROM snapshots GROUP BY venue,symbol"
    ):
        gaps = 0
        worst = 0
        previous = None
        anomalies = Counter()
        intervals = Counter()
        latest = None
        for row in db.execute(
            "SELECT body FROM snapshots WHERE venue=? AND symbol=? ORDER BY ts", (venue, symbol)
        ):
            q = json.loads(row[0])
            t = q["collected_at"]
            if previous is not None:
                worst = max(worst, t - previous)
                gaps += t - previous > 90000
            previous = t
            latest = q
            anomalies.update(quality(q, t))
            intervals[str(q["interval_h"])] += 1
        report["markets"].append(
            {
                "venue": venue,
                "symbol": symbol,
                "count": count,
                "from": first,
                "to": last,
                "span_hours": (last - first) / 3600000,
                "gaps_gt90s": gaps,
                "largest_gap_ms": worst,
                "quality_reasons_at_collection": dict(anomalies),
                "interval_h_counts": dict(intervals),
                "latest_quality_now": quality(latest, now_ms()),
            }
        )
    report["funding"] = [
        dict(r)
        for r in db.execute(
            "SELECT venue,symbol,COUNT(*) AS count,MIN(ts) AS first,MAX(ts) AS last,SUM(rate<0) AS negative FROM funding GROUP BY venue,symbol"
        )
    ]
report["status"] = "AUDITED_NOT_TRADING_APPROVED"
(ROOT / "evidence/phase2-audit.json").write_text(json.dumps(report, indent=2))
print(
    json.dumps(
        {
            "snapshots": sum(m["count"] for m in report["markets"]),
            "funding": sum(m["count"] for m in report["funding"]),
            "integrity": report["integrity"],
            "markets": report["markets"],
        },
        indent=2,
    )
)

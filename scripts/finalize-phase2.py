"""Verified final readback and isolated backup restore; production password is never parsed."""

import gzip
import hashlib
import json
import shutil
import sqlite3
import subprocess
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
before = json.loads((ROOT / "evidence/phase2-pre-deploy.json").read_text())
after = json.loads((ROOT / "evidence/phase2-post-deploy.json").read_text())
audit = json.loads((ROOT / "evidence/phase2-audit.json").read_text())
report = {
    "other_nginx_configs": len(after["nginx_files"]),
    "other_nginx_unchanged": before["nginx_files"] == after["nginx_files"],
    "pm2_count": len(after["pm2"]),
    "pm2_pid_status_unchanged": before["pm2"] == after["pm2"],
    "auth_fingerprint_unchanged": hashlib.sha256((ROOT / "secrets/auth.json").read_bytes()).hexdigest()
    == audit["auth_fingerprint"],
}
assert (
    report["other_nginx_unchanged"]
    and report["pm2_pid_status_unchanged"]
    and report["auth_fingerprint_unchanged"]
)
backup = ROOT / "data/backups/20261025T231343Z.sqlite3"
restore = ROOT / "data/restore-verification.sqlite3"
assert shutil.disk_usage(ROOT).free > 1024 * 1024 * 1024
with sqlite3.connect(backup) as source, sqlite3.connect(restore) as target:
    source.backup(target)
    assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    report["restored_backup"] = {
        "source": str(backup),
        "integrity": "ok",
        "migrations": [r[0] for r in target.execute("SELECT version FROM migrations")],
        "funding": target.execute("SELECT COUNT(*) FROM historical_funding").fetchone()[0],
        "candles": target.execute("SELECT COUNT(*) FROM candles").fetchone()[0],
    }
    assert report["restored_backup"]["funding"] == 2430 and report["restored_backup"]["candles"] == 105408
restore.unlink()
with sqlite3.connect(ROOT / "data/research.sqlite3") as db:
    funding = [
        dict(zip(["venue", "symbol", "count", "first", "last"], r, strict=True))
        for r in db.execute(
            "SELECT venue,symbol,COUNT(*),MIN(ts),MAX(ts) FROM historical_funding GROUP BY venue,symbol"
        )
    ]
    candles = [
        dict(zip(["market", "symbol", "count", "first", "last"], r, strict=True))
        for r in db.execute(
            "SELECT market,symbol,COUNT(*),MIN(ts),MAX(ts) FROM candles GROUP BY market,symbol"
        )
    ]
    report["historical_funding"] = funding
    report["historical_candles"] = candles
    report["prospective"] = {
        "hot": db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0],
        "cold": db.execute("SELECT COALESCE(SUM(count),0) FROM snapshot_archives").fetchone()[0],
        "archive_bytes": db.execute("SELECT COALESCE(SUM(bytes),0) FROM snapshot_archives").fetchone()[0],
    }
    report["monitor_totals"] = json.loads(
        db.execute("SELECT body FROM state WHERE key='hypothesis_totals'").fetchone()[0]
    )
    report["paper"] = json.loads(db.execute("SELECT body FROM state WHERE key='paper'").fetchone()[0])
    assert report["paper"]["reserved"] == 0 and len(report["paper"]["positions"]) == 1
    report["db_integrity"] = db.execute("PRAGMA integrity_check").fetchone()[0]
    assert report["db_integrity"] == "ok"
    # Real compression estimate, grouped per market; capacity projection is not observed days.
    groups = db.execute("SELECT DISTINCT venue,symbol FROM snapshots").fetchall()
    n = packed = 0
    for v, s in groups:
        rows = [
            r[0]
            for r in db.execute(
                "SELECT body FROM snapshots WHERE venue=? AND symbol=? ORDER BY ts LIMIT 500", (v, s)
            )
        ]
        raw = ("\n".join(rows) + "\n").encode()
        n += len(rows)
        packed += len(gzip.compress(raw, mtime=0))
    report["capacity_projection"] = {
        "sample_snapshots": n,
        "sample_gzip_bytes": packed,
        "projected_35d_MiB": packed / n * 6 * 2880 * 35 / 1048576,
        "observed_35_days": False,
        "archive_budget_MiB": 768,
    }
health = httpx.get("https://example.invalid/health/live", timeout=10)
health.raise_for_status()
report["health"] = health.json()
assert report["health"]["version"].startswith("0.2.0@")
report["resource_probe"] = subprocess.run(
    [
        "/usr/bin/systemctl",
        "show",
        "robo-arbitrage.service",
        "-p",
        "MemoryCurrent",
        "-p",
        "MemoryPeak",
        "-p",
        "NRestarts",
        "-p",
        "ActiveState",
    ],
    capture_output=True,
    text=True,
    check=True,
).stdout
report["status"] = "PASS"
(ROOT / "evidence/phase2-final-readback.json").write_text(json.dumps(report, indent=2))
print(
    json.dumps(
        {
            k: report[k]
            for k in [
                "status",
                "other_nginx_unchanged",
                "pm2_pid_status_unchanged",
                "auth_fingerprint_unchanged",
                "restored_backup",
                "prospective",
                "monitor_totals",
                "capacity_projection",
                "resource_probe",
            ]
        }
    )
)

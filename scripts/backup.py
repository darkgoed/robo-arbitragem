"""Bounded project-local online SQLite backup; contains virtual account state only."""

import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
dest = ROOT / "data/backups"
dest.mkdir(exist_ok=True, mode=0o700)
path = dest / (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite3")
with sqlite3.connect(ROOT / "data/research.sqlite3") as source, sqlite3.connect(path) as target:
    source.backup(target, pages=100, sleep=0.01)
    assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
os.chmod(path, 0o600)
for old in sorted(dest.glob("*.sqlite3"), reverse=True)[3:]:
    old.unlink()
print("Backup verified:", path)

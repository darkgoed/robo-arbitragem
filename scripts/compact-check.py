"""Verify lossless compaction against REAL current rows while collector stays running."""

import gzip
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robo.store import Store, now_ms
from robo.archives import compact, archived_query

ROOT = Path(__file__).resolve().parents[1]
store = Store(ROOT / "data")
with store.connect() as db:
    before = {r["id"]: json.loads(r["body"]) for r in db.execute("SELECT id,body FROM snapshots")}
paper = store.get("paper")
result = compact(store, before_ms=now_ms() - 3600000)
with store.connect() as db:
    after = {r["id"]: json.loads(r["body"]) for r in db.execute("SELECT id,body FROM snapshots")}
    manifests = db.execute("SELECT * FROM snapshot_archives").fetchall()
cold = {}
for record in manifests:
    raw = (store.directory / record["path"]).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == record["sha256"]
    for line in gzip.decompress(raw).splitlines():
        row = json.loads(line)
        cold[row["source_id"]] = row["quote"]
for identifier, q in before.items():
    assert (after.get(identifier) or cold.get(identifier)) == q, "lost/modified original snapshot"
assert store.get("paper") == paper
sample = next(iter(cold.values()))
readback = archived_query(
    store, sample["venue"], sample["symbol"], sample["collected_at"], sample["collected_at"], 500
)
assert sample in readback
report = {
    "at": now_ms(),
    "status": "PASS",
    "before_hot": len(before),
    "after_hot": len(after),
    "cold_rows": len(cold),
    "all_previous_ids_preserved": True,
    "full_L2_equal": True,
    "paper_unchanged": True,
    "archive": result,
    "query_sample_asof": sample["collected_at"],
    "note": "Collector stayed running. Counts may include new rows arriving during check. No synthetic source rows.",
}
(ROOT / "evidence/phase2-production-compaction.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))

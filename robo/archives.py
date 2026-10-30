"""Lossless bounded prospective L2 archive. Never turns candles into books."""

import gzip
import hashlib
import json
import os
from robo.store import now_ms, encode

BUDGET = 768 * 1024 * 1024
RETENTION_DAYS = 35


def _load(store, row):
    path = store.directory / row["path"]
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != row["sha256"]:
        raise ValueError("archive checksum mismatch")
    payload = gzip.decompress(raw)
    rows = [json.loads(line) for line in payload.splitlines()]
    if len(rows) != row["count"] or any(
        r["quote"]["venue"] != row["venue"] or r["quote"]["symbol"] != row["symbol"] for r in rows
    ):
        raise ValueError("archive schema/count mismatch")
    return [r["quote"] for r in rows]


def archive_stats(store):
    with store.connect() as db:
        row = db.execute(
            "SELECT COUNT(*) AS files,COALESCE(SUM(count),0) AS count,COALESCE(SUM(bytes),0) AS bytes,MIN(first_ts) AS first,MAX(last_ts) AS last FROM snapshot_archives"
        ).fetchone()
    return dict(row) | {
        "budget_bytes": BUDGET,
        "retention_days": RETENTION_DAYS,
        "kind": "EXACT_PROSPECTIVE_L2_NOT_SYNTHETIC",
    }


def compact(store, before_ms, chunk_size=500, budget_bytes=BUDGET):
    if not 1 <= chunk_size <= 500:
        raise ValueError("archive chunk bound")
    directory = store.directory / "archives"
    directory.mkdir(exist_ok=True, mode=0o700)
    state = store.get("paper", {})
    pin = min(
        (
            p["opened"]
            for p in state.get("positions", [])
            if p["status"] in {"OPEN", "RESIDUAL_SPOT"} or p.get("pending_funding")
        ),
        default=now_ms(),
    )
    expires = min(now_ms() - RETENTION_DAYS * 86400000, pin - 60000)
    with store.connect() as db:
        expired = db.execute(
            "SELECT sha256,path FROM snapshot_archives WHERE last_ts<?", (expires,)
        ).fetchall()
        for r in expired:
            db.execute("DELETE FROM snapshot_archives WHERE sha256=?", (r["sha256"],))
    for r in expired:
        (store.directory / r["path"]).unlink(missing_ok=True)
    total = sum(p.stat().st_size for p in directory.glob("*.gz"))
    archived = 0
    status = "OK"
    for _ in range(24):
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            oldest = db.execute(
                "SELECT venue,symbol FROM snapshots WHERE ts<? ORDER BY ts LIMIT 1", (before_ms,)
            ).fetchone()
            if not oldest:
                break
            rows = db.execute(
                "SELECT id,ts,body FROM snapshots WHERE venue=? AND symbol=? AND ts<? ORDER BY ts,id LIMIT ?",
                (*oldest, before_ms, chunk_size),
            ).fetchall()
            items = [{"source_id": r["id"], "quote": json.loads(r["body"])} for r in rows]
            uncompressed = ("\n".join(encode(r) for r in items) + "\n").encode()
            compressed = gzip.compress(uncompressed, compresslevel=6, mtime=0)
            digest = hashlib.sha256(compressed).hexdigest()
            target = directory / (digest + ".jsonl.gz")
            if not target.exists() and total + len(compressed) > budget_bytes:
                status = "ARCHIVE_BUDGET_BLOCKED"
                break  # Keep original rows; never silently shorten protected retention.
            existed = target.exists()
            if not existed:
                temporary = directory / (digest + ".part")
                fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(compressed)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(temporary, target)
            record = {
                "sha256": digest,
                "venue": oldest[0],
                "symbol": oldest[1],
                "first_ts": rows[0]["ts"],
                "last_ts": rows[-1]["ts"],
                "count": len(rows),
                "bytes": len(compressed),
                "path": str(target.relative_to(store.directory)),
                "created": now_ms(),
            }
            restored = _load(store, record)
            if restored != [r["quote"] for r in items]:
                raise ValueError("archive readback differs from original L2")
            db.execute(
                "INSERT OR IGNORE INTO snapshot_archives VALUES(:sha256,:venue,:symbol,:first_ts,:last_ts,:count,:bytes,:path,:created)",
                record,
            )
            db.executemany("DELETE FROM snapshots WHERE id=?", [(r["id"],) for r in rows])
            archived += len(rows)
            if not existed:
                total += len(compressed)
    result = archive_stats(store) | {"archived_rows": archived, "state": status, "hot_cutoff": before_ms}
    store.set("archive_status", result)
    return result


def archived_query(store, venue, symbol, start, end, limit=500, newest=False):
    if not 0 <= limit <= 5000:
        raise ValueError("archive query bound")
    if limit == 0:
        return []
    order = "DESC" if newest else "ASC"
    with store.connect() as db:
        files = db.execute(
            f"SELECT * FROM snapshot_archives WHERE venue=? AND symbol=? AND last_ts>=? AND first_ts<=? ORDER BY first_ts {order}",  # noqa: S608 — fixed local enum
            (venue, symbol, start, end),
        ).fetchall()  # noqa: S608 — order is a fixed local enum, values parameterized
    result = []
    for row in files:
        quotes = sorted(_load(store, row), key=lambda q: q["collected_at"], reverse=newest)
        for q in quotes:
            if start <= q["collected_at"] <= end:
                result.append(q)
                if len(result) >= limit:
                    return sorted(result, key=lambda q: q["collected_at"])
    return sorted(result, key=lambda q: q["collected_at"])

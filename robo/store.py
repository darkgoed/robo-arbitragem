"""Versioned local persistence with bounded, content-addressed public evidence."""

import gzip
import hashlib
import json
import os
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path


def now_ms():
    return int(time.time() * 1000)


def encode(value):
    return json.dumps(value, allow_nan=False, separators=(",", ":"))


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.raw = self.directory / "raw"
        self.raw.mkdir(exist_ok=True, mode=0o700)
        self.path = self.directory / "research.sqlite3"
        with self.connect() as db:
            db.execute("PRAGMA max_page_count=131072")  # 512 MiB hard database ceiling
            db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS migrations(version INTEGER PRIMARY KEY, applied INTEGER);
            INSERT OR IGNORE INTO migrations VALUES(1, strftime('%s','now'));
            CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY, venue TEXT, symbol TEXT,
              ts INTEGER, body TEXT);
            CREATE INDEX IF NOT EXISTS snapshot_lookup ON snapshots(venue,symbol,ts);
            CREATE TABLE IF NOT EXISTS funding(venue TEXT,symbol TEXT,ts INTEGER,rate REAL,
              observed INTEGER,PRIMARY KEY(venue,symbol,ts));
            CREATE TABLE IF NOT EXISTS state(key TEXT PRIMARY KEY,body TEXT);
            CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY,ts INTEGER,kind TEXT,body TEXT);
            CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY,ts INTEGER,body TEXT);
            CREATE TABLE IF NOT EXISTS snapshot_archives(sha256 TEXT PRIMARY KEY,venue TEXT,symbol TEXT,
              first_ts INTEGER,last_ts INTEGER,count INTEGER,bytes INTEGER,path TEXT UNIQUE,created INTEGER);
            CREATE INDEX IF NOT EXISTS archive_lookup ON snapshot_archives(venue,symbol,first_ts,last_ts);
            CREATE TABLE IF NOT EXISTS monitor_runs(window INTEGER PRIMARY KEY,ts INTEGER,version TEXT,
              payload BLOB,sha256 TEXT,passes INTEGER,rejects INTEGER,available INTEGER,expected INTEGER);
            CREATE TABLE IF NOT EXISTS historical_funding(venue TEXT,symbol TEXT,ts INTEGER,rate REAL,
              observed INTEGER,source TEXT,payload_sha256 TEXT,PRIMARY KEY(venue,symbol,ts));
            CREATE TABLE IF NOT EXISTS candles(venue TEXT,market TEXT,symbol TEXT,timeframe TEXT,ts INTEGER,
              open REAL,high REAL,low REAL,close REAL,volume REAL,source TEXT,checksum TEXT,ingested INTEGER,
              PRIMARY KEY(venue,market,symbol,timeframe,ts));
            CREATE TABLE IF NOT EXISTS datasets(id TEXT PRIMARY KEY,kind TEXT,source TEXT,checksum TEXT,
              first_ts INTEGER,last_ts INTEGER,count INTEGER,bytes INTEGER,ingested INTEGER,body TEXT);
            INSERT OR IGNORE INTO migrations VALUES(2, strftime('%s','now'));
            """)
        os.chmod(self.path, 0o600)

    def connect(self):
        db = sqlite3.connect(self.path, timeout=3)
        db.row_factory = sqlite3.Row
        return db

    def archive(self, url, body, latency_ms):
        raw = encode({"url": url, "collected_at": now_ms(), "latency_ms": latency_ms, "body": body}).encode()
        digest = hashlib.sha256(raw).hexdigest()
        path = self.raw / (digest + ".json.gz")
        path.write_bytes(gzip.compress(raw))
        os.chmod(path, 0o600)
        return digest

    def save_snapshot(self, q):
        with self.connect() as db:
            db.execute(
                "INSERT INTO snapshots(venue,symbol,ts,body) VALUES(?,?,?,?)",
                (q["venue"], q["symbol"], q["collected_at"], encode(q)),
            )

    def snapshots(self, venue, symbol, limit=1000):
        with self.connect() as db:
            rows = db.execute(
                "SELECT body FROM snapshots WHERE venue=? AND symbol=? ORDER BY ts DESC LIMIT ?",
                (venue, symbol, limit),
            ).fetchall()
        hot = [json.loads(r[0]) for r in reversed(rows)]
        if len(hot) < limit:
            from robo.archives import archived_query

            cold = archived_query(self, venue, symbol, 0, 2**63 - 1, limit - len(hot), newest=True)
            return sorted(cold + hot, key=lambda q: q["collected_at"])[-limit:]
        return hot

    def history(self, venue, symbol):
        with self.connect() as db:
            return [
                dict(r)
                for r in db.execute(
                    "SELECT * FROM funding WHERE venue=? AND symbol=? ORDER BY ts", (venue, symbol)
                )
            ]

    def save_funding(self, venue, symbol, rates):
        with self.connect() as db:
            db.executemany(
                "INSERT OR IGNORE INTO funding VALUES(?,?,?,?,?)",
                [(venue, symbol, r["timestamp"], r["fundingRate"], now_ms()) for r in rates],
            )

    def get(self, key, default=None):
        with self.connect() as db:
            row = db.execute("SELECT body FROM state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def set(self, key, value):
        with self.connect() as db:
            db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, encode(value)))

    @contextmanager
    def mutate(self, key, default):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM state WHERE key=?", (key,)).fetchone()
            value = json.loads(row[0]) if row else default
            yield value
            db.execute("INSERT OR REPLACE INTO state VALUES(?,?)", (key, encode(value)))

    def event(self, kind, value):
        with self.connect() as db:
            db.execute("INSERT INTO events(ts,kind,body) VALUES(?,?,?)", (now_ms(), kind, encode(value)))

    def events(self, limit=100):
        with self.connect() as db:
            return [
                dict(r) | {"body": json.loads(r["body"])}
                for r in db.execute("SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))
            ]

    def experiment(self, report):
        with self.connect() as db:
            db.execute("INSERT INTO experiments VALUES(?,?,?)", (report["id"], now_ms(), encode(report)))

    def experiments(self):
        with self.connect() as db:
            return [
                json.loads(r[0]) for r in db.execute("SELECT body FROM experiments ORDER BY ts DESC LIMIT 30")
            ]

    def maintenance(self):
        cutoff = now_ms()
        from robo.archives import compact

        archive_result = compact(self, before_ms=cutoff - 86400000)
        with self.connect() as db:
            # Full prospective L2 is losslessly compacted before any hot-row deletion.
            # Historical datasets are independent of this hot cache's retention.
            db.execute("DELETE FROM monitor_runs WHERE ts<?", (cutoff - 35 * 86400000,))
            db.execute("DELETE FROM funding WHERE ts<?", (cutoff - 90 * 86400000,))
            db.execute("DELETE FROM historical_funding WHERE ts<?", (cutoff - 730 * 86400000,))
            db.execute("DELETE FROM candles WHERE ts<?", (cutoff - 730 * 86400000,))
            db.execute("DELETE FROM datasets WHERE ingested<?", (cutoff - 730 * 86400000,))
            db.execute("DELETE FROM events WHERE id < (SELECT COALESCE(MAX(id),0)-5000 FROM events)")
            db.execute(
                "DELETE FROM experiments WHERE id NOT IN (SELECT id FROM experiments ORDER BY ts DESC LIMIT 30)"
            )
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        files = sorted(self.raw.glob("*.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
        used = 0
        for path in files:
            size = path.stat().st_size
            if used + size > 32 * 1024 * 1024 or time.time() - path.stat().st_mtime > 7 * 86400:
                path.unlink()
            else:
                used += size
        return {"db_bytes": self.path.stat().st_size, "raw_bytes": used, "archives": archive_result}

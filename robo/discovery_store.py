"""Separate research-only SQLite and lossless verified cold archive; never prunes evidence."""
import gzip
import hashlib
import json
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from robo.store import encode, now_ms

BUDGET = 256 * 1024 * 1024


@contextmanager
def connect(path):
    # These PRAGMAs are CONNECTION-local: apply to every writer, not only setup.
    db = sqlite3.connect(path, timeout=3)
    try:
        db.execute('PRAGMA max_page_count=16384')
        db.execute('PRAGMA journal_size_limit=4194304')
        with db:
            yield db
    finally:
        db.close()


def setup(home):
    path = Path(home) / 'data/discovery.sqlite3'
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with connect(path) as db:
        db.executescript('''
        PRAGMA journal_mode=WAL;
        PRAGMA journal_size_limit=4194304;
        PRAGMA max_page_count=16384;
        CREATE TABLE IF NOT EXISTS discovery_runs(id TEXT PRIMARY KEY,ts INTEGER,finished INTEGER,expected INTEGER,collected INTEGER,hypotheses INTEGER);
        CREATE TABLE IF NOT EXISTS discovery_observations(run_id TEXT,venue TEXT,base TEXT,received_at INTEGER,status TEXT,body TEXT,sha256 TEXT,PRIMARY KEY(run_id,venue,base));
        CREATE TABLE IF NOT EXISTS discovery_payloads(run_id TEXT PRIMARY KEY,payload BLOB,sha256 TEXT);
        CREATE TABLE IF NOT EXISTS discovery_archives(run_id TEXT PRIMARY KEY,ts INTEGER,path TEXT,sha256 TEXT,bytes INTEGER);
        ''')
    path.chmod(0o600)
    return path


def record(path, rid, venue, base, data):
    body = encode(data)
    with connect(path) as db:
        previous = db.execute('SELECT sha256 FROM discovery_observations WHERE run_id=? AND venue=? AND base=?', (rid, venue, base)).fetchone()
        if previous and previous[0] != hashlib.sha256(body.encode()).hexdigest():
            raise ValueError('DISCOVERY_IDEMPOTENCY_CONFLICT')
        db.execute('INSERT OR IGNORE INTO discovery_observations VALUES(?,?,?,?,?,?,?)',
                   (rid, venue, base, now_ms(), data['result']['status'], body, hashlib.sha256(body.encode()).hexdigest()))


def store_payload(path, rid, payload):
    raw = gzip.compress(encode(payload).encode(), mtime=0)
    with connect(path) as db:
        db.execute('INSERT INTO discovery_payloads VALUES(?,?,?)', (rid, raw, hashlib.sha256(raw).hexdigest()))


def verified(raw, sha):
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError('DISCOVERY_CHECKSUM_MISMATCH')
    return json.loads(gzip.decompress(raw))


def compact(path, keep=6, budget=BUDGET):
    directory = path.parent / 'discovery-archives'
    directory.mkdir(exist_ok=True, mode=0o700)
    used = sum(p.stat().st_size for p in directory.glob('*.gz'))
    with connect(path) as db:
        rows = db.execute('SELECT id,ts FROM discovery_runs WHERE finished IS NOT NULL ORDER BY ts DESC LIMIT -1 OFFSET ?', (keep,)).fetchall()
        for rid, ts in rows:
            run = db.execute('SELECT * FROM discovery_runs WHERE id=?', (rid,)).fetchone()
            obs = db.execute('SELECT * FROM discovery_observations WHERE run_id=? ORDER BY venue,base', (rid,)).fetchall()
            if any(hashlib.sha256(r[5].encode()).hexdigest() != r[6] for r in obs):
                raise ValueError('DISCOVERY_CHECKSUM_MISMATCH')
            payload = db.execute('SELECT payload,sha256 FROM discovery_payloads WHERE run_id=?', (rid,)).fetchone()
            data = {'run': run, 'observations': obs, 'payload': verified(*payload) if payload else None}
            raw = gzip.compress(encode(data).encode(), mtime=0)
            sha = hashlib.sha256(raw).hexdigest()
            target = directory / (sha + '.json.gz')
            if not target.exists() and used + len(raw) > budget:
                raise ValueError('DISCOVERY_ARCHIVE_BUDGET_BLOCKED')
            if not target.exists():
                temporary = target.with_suffix('.part')
                with temporary.open('wb') as stream:
                    stream.write(raw)
                    stream.flush()
                    os.fsync(stream.fileno())
                temporary.chmod(0o600)
                temporary.replace(target)
                used += len(raw)
            restored = verified(target.read_bytes(), sha)
            assert restored == json.loads(encode(data)), 'cold readback differs'
            db.execute('INSERT OR IGNORE INTO discovery_archives VALUES(?,?,?,?,?)', (rid, ts, target.name, sha, len(raw)))
            db.execute('DELETE FROM discovery_observations WHERE run_id=?', (rid,))
            db.execute('DELETE FROM discovery_payloads WHERE run_id=?', (rid,))
            db.execute('DELETE FROM discovery_runs WHERE id=?', (rid,))
    return {'archive_bytes': used, 'budget_bytes': budget, 'retention': 'NO_EXPIRY_STOP_AT_QUOTA'}


def history(home, limit=24):
    path = Path(home) / 'data/discovery.sqlite3'
    if not path.exists():
        return []
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1) as db:
        hot = [{'id': r[0], 'ts': r[1], 'finished': r[2], 'cold': False} for r in db.execute('SELECT id,ts,finished FROM discovery_runs WHERE finished IS NOT NULL ORDER BY ts DESC LIMIT ?', (limit,))]
        cold = [{'id': r[0], 'ts': r[1], 'cold': True} for r in db.execute('SELECT run_id,ts FROM discovery_archives ORDER BY ts DESC LIMIT ?', (limit,))]
    return sorted(hot + cold, key=lambda r: r['ts'], reverse=True)[:limit]


def detail(home, rid):
    path = Path(home) / 'data/discovery.sqlite3'
    if not path.exists():
        return None
    with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1) as db:
        row = db.execute('SELECT payload,sha256 FROM discovery_payloads WHERE run_id=?', (rid,)).fetchone()
        if row:
            return verified(*row)
        row = db.execute('SELECT path,sha256 FROM discovery_archives WHERE run_id=?', (rid,)).fetchone()
        if row:
            target = path.parent / 'discovery-archives' / row[0]
            if target.parent.resolve() != (path.parent / 'discovery-archives').resolve():
                raise ValueError('INVALID_ARCHIVE_PATH')
            return verified(target.read_bytes(), row[1])['payload']
    return None

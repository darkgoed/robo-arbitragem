"""Read-only preservation gate against the verified pre-expansion online backup."""
import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check():
    manifest = json.loads((ROOT / 'evidence/expansion-checkpoint.json').read_text())
    before = Path(manifest['databases']['research']['path'])
    with sqlite3.connect(f"file:{ROOT / 'data/research.sqlite3'}?mode=ro", uri=True) as db:
        db.execute('ATTACH DATABASE ? AS baseline', (str(before),))
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        # Fixed table names. Exact original rows must still exist, not just equal counts.
        counts = {}
        for table in ('candles', 'historical_funding', 'monitor_runs', 'datasets', 'experiments'):
            missing = db.execute(f'SELECT COUNT(*) FROM (SELECT * FROM baseline.{table} EXCEPT SELECT * FROM main.{table})').fetchone()[0]  # noqa: S608 — fixed enum
            assert missing == 0, (table, missing)
            counts[table] = db.execute(f'SELECT COUNT(*) FROM main.{table}').fetchone()[0]  # noqa: S608 — fixed enum
        state = dict(db.execute('SELECT key,body FROM state'))
        assert state['paper'] == manifest['state']['paper'], 'legacy paper changed'
        paper = json.loads(state['paper'])
        assert paper['balance'] == 999.304521645952
        expected = {hashlib.sha256(r[0].encode()).hexdigest() for r in db.execute('SELECT body FROM baseline.snapshots')}
        actual = {hashlib.sha256(r[0].encode()).hexdigest() for r in db.execute('SELECT body FROM main.snapshots')}
        archived = db.execute('SELECT path,sha256,count FROM main.snapshot_archives').fetchall()
        for rel, sha, count in archived:
            raw = (ROOT / 'data' / rel).read_bytes()
            assert hashlib.sha256(raw).hexdigest() == sha
            rows = [json.loads(line) for line in gzip.decompress(raw).splitlines()]
            assert len(rows) == count
            actual.update(hashlib.sha256(json.dumps(r['quote'], allow_nan=False, separators=(',', ':')).encode()).hexdigest() for r in rows)
        assert expected <= actual, 'old hot snapshots lost from hot+cold union'
        paths = {r[0]: r[1] for r in archived}
        assert all(paths.get(r['path']) == r['sha256'] for r in manifest['cold'])
    report = {'status': 'PASS', 'paper_unchanged': True, 'paper_balance': paper['balance'],
              'original_hot_snapshots_preserved': len(expected), 'original_cold_archives_preserved': len(manifest['cold']),
              'current_archive_files_verified': len(archived), 'tables_original_rows_preserved': counts,
              'research_integrity': 'ok', 'production_secrets': 'NOT_READ_OR_MODIFIED'}
    (ROOT / 'evidence/expansion-preservation.json').write_text(json.dumps(report, indent=2))
    return report


if __name__ == '__main__':
    print(json.dumps(check(), indent=2))

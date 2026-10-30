"""Project-scoped online backup and public-data preservation inventory. No secrets."""
import gzip
import hashlib
import json
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def checkpoint():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    dest = ROOT / 'data' / 'expansion-backups' / stamp
    dest.mkdir(parents=True, mode=0o700)
    report = {'at': stamp, 'directory': str(dest), 'databases': {}, 'cold': []}
    manifests = []
    for name in ('research', 'discovery'):
        source = ROOT / 'data' / (name + '.sqlite3')
        if not source.exists():
            continue
        target = dest / source.name
        with sqlite3.connect(f'file:{source}?mode=ro', uri=True) as db, sqlite3.connect(target) as backup:
            db.backup(backup, pages=100, sleep=0.01)
            assert backup.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
            if name == 'research':
                report['state'] = dict(backup.execute('SELECT key,body FROM state'))
                report['tables'] = {t: backup.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0]  # noqa: S608 — fixed internal tuple, no user input
                                    for t in ('historical_funding', 'candles', 'monitor_runs', 'snapshots', 'snapshot_archives')}
                manifests = backup.execute('SELECT path,sha256,count FROM snapshot_archives').fetchall()
        target.chmod(0o600)
        # Reopen the restored copy, not just the live backup handle.
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as restored:
            assert restored.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        report['databases'][name] = {'path': str(target), 'sha256': digest(target), 'restore_integrity': 'ok'}
    for rel, sha, count in manifests:
        source = ROOT / 'data' / rel
        assert digest(source) == sha
        assert len(gzip.decompress(source.read_bytes()).splitlines()) == count
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        shutil.copy2(source, target)
        assert digest(target) == sha
        report['cold'].append({'path': rel, 'sha256': sha, 'count': count})
    # Draft originals retained separately, without modifying them.
    for rel in ('robo/discovery.py', 'scripts/discover.py', 'tests/test_discovery.py'):
        target = dest / 'drafts' / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / rel, target)
    path = ROOT / 'evidence' / 'expansion-checkpoint.json'
    path.write_text(json.dumps(report, indent=2))
    print(json.dumps({'backup': str(dest), 'databases': report['databases'], 'cold_files': len(report['cold']), 'tables': report['tables']}))


if __name__ == '__main__':
    checkpoint()

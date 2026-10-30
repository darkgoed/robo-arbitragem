"""Own-service-only release with online backup, exact readback and code-only rollback."""
import gzip
import hashlib
import json
import sqlite3
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx
from robo.discovery_store import history, detail
from robo.discovery_scanner import Scanner
from robo.engine import quality

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://example.invalid'
BASELINE = '76199d90df738cc250d7755b8c6558187333a0fe'
SERVICE = 'robo-arbitrage.service'


def command(args, timeout=45):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=timeout, check=False)  # noqa: S603 — project-scoped fixed commands
    if result.returncode:
        raise RuntimeError('command failed: ' + ' '.join(args) + ' ' + result.stderr[:250])
    return result.stdout.strip()


def service():
    raw = command(['/usr/bin/systemctl', 'show', SERVICE, '-p', 'MainPID', '-p', 'MemoryCurrent', '-p', 'MemoryPeak',
                   '-p', 'TasksCurrent', '-p', 'Result', '-p', 'ActiveState', '-p', 'NRestarts'])
    return dict(line.split('=', 1) for line in raw.splitlines())


def auth_metadata():
    # Filesystem metadata only. Never read/hash/reset the production authentication contents.
    stat = (ROOT / 'secrets/auth.json').stat()
    return {'inode': stat.st_ino, 'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'mode': stat.st_mode}


def backup():
    dest = ROOT / 'data/expansion-backups' / ('predeploy-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
    dest.mkdir(mode=0o700)
    results = {}
    for name in ('research', 'discovery'):
        target = dest / (name + '.sqlite3')
        with sqlite3.connect(f"file:{ROOT / 'data' / (name + '.sqlite3')}?mode=ro", uri=True) as source, sqlite3.connect(target) as restored:
            source.backup(restored, pages=100, sleep=0.01)
        target.chmod(0o600)
        with sqlite3.connect(f'file:{target}?mode=ro', uri=True) as reopened:
            assert reopened.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        results[name] = {'path': str(target), 'sha256': hashlib.sha256(target.read_bytes()).hexdigest(), 'restored_integrity': 'ok'}
    return results


def code_rollback():
    # Only entry points + VERSION. Old code never imports new modules; keep all evidence/data.
    restored = {}
    for rel in ('robo/app.py', 'web/index.html', 'VERSION'):
        old = subprocess.run(['/usr/bin/git', 'show', BASELINE + ':' + rel], cwd=ROOT, capture_output=True, check=True).stdout  # noqa: S603 — fixed baseline/paths
        (ROOT / rel).write_bytes(old)
        restored[rel] = hashlib.sha256(old).hexdigest()
    command(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'restart', SERVICE])
    with httpx.Client(timeout=8, trust_env=False) as client:
        deadline = time.monotonic() + 35
        while True:
            try:
                r = client.get(BASE + '/health/live')
                if r.status_code == 200 and r.json()['version'].startswith('0.2.0@'):
                    return {'files': restored, 'health': r.json(), 'data_restored_over_live': False}
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, 'rollback startup failed'
            time.sleep(0.5)


def verify_live(candidate, started):
    report = {'public_paths': {}, 'positive_production_login': 'NOT_EXERCISED: production password preserved; positive real-data staging E2E passed'}
    ready_observed = False
    deadline = time.monotonic() + 180
    with httpx.Client(timeout=8, trust_env=False, follow_redirects=False) as client:
        while True:
            try:
                r = client.get(BASE + '/health/live')
                if r.status_code == 200 and r.json()['version'] == '0.3.0@' + candidate:
                    assert r.json()['mode'] == 'PAPER_ONLY' and r.json()['real_trading'] == 'NOT_IMPLEMENTED'
                    report['health'] = r.json()
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, 'production startup timeout'
            time.sleep(0.5)
        for path in ('/api/discovery', '/api/discovery/history', '/api/overview', '/api/monitor', '/health/ready', '/data/discovery.sqlite3', '/secrets/auth.json'):
            r = client.get(BASE + path)
            assert r.status_code == 401, (path, r.status_code)
            assert r.headers['x-content-type-options'] == 'nosniff' and 'no-store' in r.headers['cache-control']
            report['public_paths'][path] = r.status_code
        html = client.get(BASE + '/')
        assert html.status_code == 200 and 'id="discovery"' in html.text
        for asset in ('discovery.js', 'app.js', 'phase2.js', 'style.css'):
            r = client.get(BASE + '/assets/' + asset)
            assert r.status_code == 200
            assert r.content == (ROOT / 'web' / asset).read_bytes()
            report['public_paths']['/assets/' + asset] = {'status': 200, 'sha256': hashlib.sha256(r.content).hexdigest()}
        while True:
            with sqlite3.connect(f"file:{ROOT / 'data/research.sqlite3'}?mode=ro", uri=True) as db:
                rows = db.execute('SELECT body FROM snapshots WHERE id IN (SELECT MAX(id) FROM snapshots GROUP BY venue,symbol)').fetchall()
                fresh = sum(not quality(json.loads(row[0]), int(time.time() * 1000)) for row in rows)
                cycles = db.execute('SELECT venue,symbol,COUNT(*) FROM snapshots WHERE ts>=? GROUP BY venue,symbol', (started,)).fetchall()
                raw, sha = db.execute('SELECT payload,sha256 FROM monitor_runs ORDER BY ts DESC LIMIT 1').fetchone()
                assert hashlib.sha256(raw).hexdigest() == sha
                monitor = json.loads(gzip.decompress(raw))
                assert monitor['brl5']['balance'] == 5 and monitor['brl5']['fills'] == 0
                assert monitor['shadow_brl1000']['balance'] == 1000 and monitor['shadow_brl1000']['fills'] == 0
            ready_observed |= fresh == 6
            runs = history(ROOT, 1)
            payload = detail(ROOT, runs[0]['id']) if runs else None
            new_capture = payload is not None and payload['ts'] >= started and payload['model_source_sha256'] == Scanner(ROOT).code_sha256
            if new_capture and ready_observed and len(cycles) == 6 and all(r[2] >= 2 for r in cycles):
                assert all(h['pairs'] == 8 and h['errors'] == 0 for h in payload['health'].values())
                report.update(production_capture_run=payload['id'], production_capture_health=payload['health'],
                              model_source_sha256=payload['model_source_sha256'], six_fresh_observed=ready_observed,
                              two_new_legacy_cycles_per_market=cycles, brl5=monitor['brl5'], shadow_brl1000=monitor['shadow_brl1000'])
                break
            assert time.monotonic() < deadline, 'new production capture / legacy cycles not verified'
            time.sleep(1)
    report['service'] = service()
    assert report['service']['ActiveState'] == 'active' and report['service']['Result'] == 'success'
    assert int(report['service']['MemoryCurrent']) < 400 * 1024 * 1024
    assert int(report['service']['TasksCurrent']) < 48
    return report


def main():
    gates = json.loads((ROOT / 'evidence/expansion-gates.json').read_text())
    assert gates['status'] == 'PASS'
    assert json.loads((ROOT / 'evidence/expansion-rollback-rehearsal.json').read_text())['status'] == 'PASS'
    assert gates['real_capture']['model_source_sha256'] == Scanner(ROOT).code_sha256
    command(['/usr/bin/git', 'diff', '--exit-code', '--', 'robo', 'web', 'VERSION'])
    candidate = command(['/usr/bin/git', 'rev-parse', 'HEAD'])
    before_auth = auth_metadata()
    report = {'candidate': candidate, 'baseline': BASELINE, 'before_service': service(),
              'production_credentials': 'CONTENTS_NOT_READ_OR_MODIFIED', 'started': time.time(), 'backups': backup()}
    restarted = False
    try:
        started = int(time.time() * 1000)
        restarted = True
        command(['/usr/bin/sudo', '-n', '/usr/bin/systemctl', 'restart', SERVICE])
        report['verification'] = verify_live(candidate, started)
        assert auth_metadata() == before_auth
        command([str(ROOT / '.venv/bin/python'), 'scripts/expansion-preservation.py'])
        # Read back exact external service target again after all side effects.
        report['final_service'] = service()
        assert report['final_service']['ActiveState'] == 'active'
        report.update(status='PASS', auth_file_metadata_unchanged=True, finished=time.time())
    except Exception as exc:
        report.update(status='FAIL', failure=str(exc), finished=time.time())
        if restarted:
            report['rollback'] = code_rollback()
        raise
    finally:
        (ROOT / 'evidence/expansion-deploy.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

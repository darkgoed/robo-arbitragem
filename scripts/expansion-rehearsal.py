"""Actual candidate -> baseline -> candidate startup drill, isolated real-data stage only."""
import gzip
import hashlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
HOME = ROOT / 'data/expansion-stage'
BASELINE = '76199d90df738cc250d7755b8c6558187333a0fe'


def protected():
    with sqlite3.connect(f"file:{HOME / 'data/research.sqlite3'}?mode=ro", uri=True) as db:
        return db.execute("SELECT body FROM state WHERE key='paper'").fetchone()[0]


def health(client, deadline):
    while time.monotonic() < deadline:
        try:
            response = client.get('/health/live')
            if response.status_code == 200:
                return response.json()
        except httpx.TransportError:
            pass
        time.sleep(0.2)
    raise RuntimeError('stage startup timeout')


def run(code, candidate):
    # No source or secret is changed; only the isolated stage VERSION follows its code artifact.
    (HOME / 'VERSION').write_text((code / 'VERSION').read_text())
    previous = None
    if candidate:
        with sqlite3.connect(f"file:{HOME / 'data/discovery.sqlite3'}?mode=ro", uri=True) as db:
            previous = db.execute('SELECT id FROM discovery_runs WHERE finished IS NOT NULL ORDER BY ts DESC LIMIT 1').fetchone()[0]
    env = os.environ | {'ROBO_HOME': str(HOME), 'PYTHONPATH': str(code)}
    command = [sys.executable, '-c', "import uvicorn;from robo.app import create_app;uvicorn.run(create_app(collect=True,test_origin='http://localhost:3216'),host='127.0.0.1',port=3216,access_log=False)"]
    logfile = ROOT / 'data' / ('expansion-rehearsal-' + ('candidate' if candidate else 'baseline') + '.log')
    with logfile.open('a') as output:
        proc = subprocess.Popen(command, cwd=code, env=env, stdout=output, stderr=output)  # noqa: S603 — fixed child in project scope
        try:
            with httpx.Client(base_url='http://localhost:3216', timeout=5, trust_env=False) as client:
                h = health(client, time.monotonic() + 35)
                assert h['mode'] == 'PAPER_ONLY' and h['real_trading'] == 'NOT_IMPLEMENTED'
                assert h['version'].startswith('0.3.0@' if candidate else '0.2.0@')
                assert client.get('/api/discovery').status_code == 401
                # Only the isolated TEST password is read, never production auth.
                login = client.post('/auth/login', headers={'Origin': 'http://localhost:3216'},
                                    json={'username': 'admin', 'password': (HOME / 'secrets/initial-password.txt').read_text().strip()})
                assert login.status_code == 200
                deadline = time.monotonic() + 150
                # Freshness legitimately alternates between 30s collections (15s TTL).
                while True:
                    ready = client.get('/health/ready')
                    if ready.status_code == 200 and ready.json()['fresh_symbols'] == 6:
                        break
                    assert time.monotonic() < deadline, 'baseline 6 fresh sources not observed'
                    time.sleep(1)
                readiness_observed = ready.json()
                if candidate:
                    while True:
                        result = client.get('/api/discovery')
                        assert result.status_code == 200
                        value = result.json()
                        if value['ready'] and value['id'] != previous and not value['worker']['running']:
                            assert value['worker']['last_error'] is None
                            assert all(v['pairs'] == 8 for v in value['health'].values())
                            break
                        assert time.monotonic() < deadline, 'candidate real capture timeout'
                        time.sleep(1.5)
                    browser = subprocess.run([shutil.which('node'), 'scripts/expansion-browser.cjs'], cwd=ROOT, capture_output=True, text=True, timeout=90, check=False)  # noqa: S603 — fixed project verifier
                    assert browser.returncode == 0, browser.stdout + browser.stderr
                    assert client.get('/api/discovery/history').status_code == 200
                else:
                    assert client.get('/api/discovery').status_code == 404
                    assert 'id="discovery"' not in client.get('/').text
                ready = client.get('/health/ready')
                assert ready.status_code in (200, 503)
                mem = {line.split(':')[0]: line.split(':')[1].strip() for line in Path(f'/proc/{proc.pid}/status').read_text().splitlines() if line.startswith(('VmRSS:', 'VmHWM:'))}
                assert int(mem['VmHWM'].split()[0]) < 400 * 1024, mem
                monitor = client.get('/api/monitor').json()
                assert monitor['brl5']['balance'] == 5 and monitor['brl5']['fills'] == 0
                assert monitor['latest']['shadow_brl1000']['balance'] == 1000
                return {'health': h, 'positive_test_auth': True, 'ready': ready.json(), 'six_fresh_observed': readiness_observed, 'memory': mem,
                        'real_capture_run': value['id'] if candidate else None, 'api_discovery': 200 if candidate else 404,
                        'browser': 'PASS' if candidate else 'baseline asset verified'}
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=20)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


def main():
    baseline = ROOT / 'data/expansion-baseline'
    baseline.mkdir(exist_ok=True, mode=0o700)
    artifact = subprocess.run(['/usr/bin/git', 'archive', BASELINE, 'robo', 'web', 'VERSION'], cwd=ROOT, capture_output=True, check=True).stdout  # noqa: S603 — fixed git arguments
    with tarfile.open(fileobj=io.BytesIO(artifact)) as archive:
        archive.extractall(baseline, filter='data')
    before = protected()
    report = {'scope': 'ISOLATED_STAGE_ONLY', 'baseline': BASELINE,
              'baseline_code_sha256': hashlib.sha256(artifact).hexdigest(), 'started': time.time()}
    try:
        report['candidate_first'] = run(ROOT, True)
        assert protected() == before
        report['baseline_rollback'] = run(baseline, False)
        assert protected() == before
        report['candidate_restored'] = run(ROOT, True)
        assert protected() == before
        # Cold payload can be recovered after real restart using the same unchanged DB.
        with sqlite3.connect(f"file:{HOME / 'data/discovery.sqlite3'}?mode=ro", uri=True) as db:
            for payload, sha in db.execute('SELECT payload,sha256 FROM discovery_payloads'):
                assert hashlib.sha256(payload).hexdigest() == sha
                assert json.loads(gzip.decompress(payload))['fills'] == 0
        report.update(status='PASS', paper_unchanged=True, production_auth='NOT_ACCESSED')
    except Exception as exc:
        report.update(status='FAIL', failure=str(exc))
        raise
    finally:
        report['finished'] = time.time()
        (ROOT / 'evidence/expansion-rollback-rehearsal.json').write_text(json.dumps(report, indent=2))
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

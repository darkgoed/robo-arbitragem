"""Final read-only production gate after survey: hashes, source quality and legacy safety."""
import gzip
import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
from robo.discovery_store import history, detail
from robo.discovery_scanner import Scanner
from robo.engine import quality

ROOT = Path(__file__).resolve().parents[1]


def main():
    deploy = json.loads((ROOT / 'evidence/expansion-deploy.json').read_text())
    assert deploy['status'] == 'PASS'
    report = {'at': time.time(), 'release_commit': deploy['candidate'], 'scope': 'READ_ONLY_PRODUCTION',
              'production_password': 'NEVER_READ', 'live': False, 'real_fills': 0, 'real_pnl': 0}
    with httpx.Client(timeout=8, trust_env=False) as client:
        r = client.get('https://example.invalid/health/live')
        assert r.status_code == 200
        assert r.json()['version'] == '0.3.0@' + deploy['candidate']
        assert r.json()['real_trading'] == 'NOT_IMPLEMENTED'
        report['health'] = r.json()
        report['anonymous'] = {p: client.get('https://example.invalid' + p).status_code
                               for p in ('/api/discovery', '/api/discovery/history', '/api/monitor', '/health/ready')}
        assert all(v == 401 for v in report['anonymous'].values())
    run = detail(ROOT, history(ROOT, 1)[0]['id'])
    assert run['model_source_sha256'] == Scanner(ROOT).code_sha256
    assert time.time() * 1000 - run['finished'] < 17 * 60000
    assert len(run['samples']) == 40 and len(run['decisions']) == 1080
    assert all(v['pairs'] == 8 for v in run['health'].values())
    assert not run['live'] and run['fills'] == run['real_pnl'] == 0
    report['last_real_capture'] = {'id': run['id'], 'health': run['health'], 'finished': run['finished'],
                                   'decisions': len(run['decisions']), 'model_source_sha256': run['model_source_sha256']}
    deadline = time.monotonic() + 45
    while True:
        with sqlite3.connect(f"file:{ROOT / 'data/research.sqlite3'}?mode=ro", uri=True) as db:
            rows = db.execute('SELECT body FROM snapshots WHERE id IN (SELECT MAX(id) FROM snapshots GROUP BY venue,symbol)').fetchall()
            fresh = sum(not quality(json.loads(r[0]), int(time.time() * 1000)) for r in rows)
            paper = json.loads(db.execute("SELECT body FROM state WHERE key='paper'").fetchone()[0])
            raw, sha = db.execute('SELECT payload,sha256 FROM monitor_runs ORDER BY ts DESC LIMIT 1').fetchone()
            assert hashlib.sha256(raw).hexdigest() == sha
            monitor = json.loads(gzip.decompress(raw))
        if fresh == 6:
            break
        assert time.monotonic() < deadline, 'six fresh legacy sources not observed'
        time.sleep(1)
    assert paper['balance'] == 999.304521645952
    assert monitor['brl5']['balance'] == 5 and monitor['brl5']['fills'] == 0
    assert monitor['shadow_brl1000']['balance'] == 1000 and monitor['shadow_brl1000']['fills'] == 0
    report.update(six_fresh_legacy_sources=fresh, paper_balance=paper['balance'],
                  brl5=monitor['brl5'], shadow_balance=monitor['shadow_brl1000']['balance'])
    proc = subprocess.run(['/usr/bin/systemctl', 'show', 'robo-arbitrage.service', '-p', 'ActiveState', '-p', 'MemoryCurrent',
                           '-p', 'MemoryPeak', '-p', 'NRestarts', '-p', 'TasksCurrent', '-p', 'Result'],
                          capture_output=True, text=True, timeout=10, check=True)  # noqa: S603 — exact own-service target
    state = dict(line.split('=', 1) for line in proc.stdout.splitlines())
    assert state['ActiveState'] == 'active' and state['Result'] == 'success'
    assert int(state['MemoryCurrent']) < 400 * 1024 * 1024 and state['NRestarts'] == '0'
    report['service'] = state
    report['recent_project_logs'] = (ROOT / 'logs/app.log').read_text().splitlines()[-12:]
    assert not any('cycle_failed' in line or 'discovery_cycle_failed' in line for line in report['recent_project_logs'])
    report['status'] = 'PASS'
    (ROOT / 'evidence/expansion-final-live.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k:v for k,v in report.items() if k not in {'last_real_capture','recent_project_logs'}}, indent=2))


if __name__ == '__main__':
    main()

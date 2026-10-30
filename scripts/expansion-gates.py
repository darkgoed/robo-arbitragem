"""Reproducible full gates and current real-public capture audit. No credentials read."""
import ast
import gzip
import hashlib
import json
import resource
import sqlite3
import subprocess
import time
from collections import Counter
from pathlib import Path

from robo.discovery import VENUES, BASES
from robo.discovery_store import history, detail
from robo.discovery_transport import public_only
from robo.discovery_scanner import Scanner

ROOT = Path(__file__).resolve().parents[1]


def capture_audit(home=ROOT):
    run = history(home, 1)[0]
    p = detail(home, run['id'])
    assert p['origin'] == 'OBSERVED_PUBLIC_REST'
    assert p['model_source_sha256'] == Scanner(home).code_sha256, 'capture predates current model code'
    assert len(p['samples']) == len(VENUES) * len(BASES)
    expected = (len(VENUES) + 2 * len(VENUES) * (len(VENUES) - 1)) * len(BASES) * len(p['scenarios_brl'])
    assert len(p['decisions']) == expected
    assert len({(s['venue'], s['base']) for s in p['samples']}) == len(p['samples'])
    assert p['fills'] == 0 and p['real_pnl'] == 0 and not p['live'] and not p['auto_open']
    assert 'SYNTHETIC_TEST' not in json.dumps(p)
    source_quality = {}
    for venue in VENUES:
        samples = [s for s in p['samples'] if s['venue'] == venue]
        for s in samples:
            assert s.get('spot') and s.get('perp') and s.get('funding'), (venue, s.get('error'))
            assert s['spot_market']['active'] and s['perp_market']['active']
            assert s['perp_market']['linear'] and s['perp_market']['contractSize'] > 0
            assert s['perp_market']['settle'] == 'USDT'
            assert s['spot_market']['quote'] == s['perp_market']['quote'] == 'USDT'
        source_quality[venue] = {'pairs_observed': len(samples),
                                 'spot_source_timestamps': sum(s['spot']['timestamp'] is not None for s in samples),
                                 'perp_source_timestamps': sum(s['perp']['timestamp'] is not None for s in samples),
                                 'funding_asof_available': sum(s['funding']['source_asof'] is not None for s in samples),
                                 'contract_sizes_base': {s['base']: s['perp_market']['contractSize'] for s in samples}}
        for call in p['http'][venue]:
            assert call['method'] == 'GET' and public_only(venue, call['url'])
            assert call.get('http_status') == 200 and call.get('sha256') and 'error' not in call
        assert len(p['http'][venue]) <= 32
    by_strategy = {}
    for strategy in ('A', 'B', 'C'):
        rows = [d for d in p['decisions'] if d['strategy'] == strategy]
        assert all(d['fills'] == 0 and d['baseline_no_trade_pnl'] == 0 for d in rows)
        for d in rows:
            if d['status'] == 'HYPOTHESIS':
                e = d['estimate']
                assert e.get('net_one_event_usdt', e.get('net_24h_usdt', -1)) > 0
                assert not d['reasons'] and e['capital_required_brl'] <= d['budget_brl']
            if d['venue'] == 'binance' and strategy in {'A', 'B'}:
                assert d['status'] == 'REJECT'
        by_strategy[strategy] = {'decisions': len(rows), 'hypotheses': sum(d['status'] == 'HYPOTHESIS' for d in rows),
                                 'estimates_available': sum(d['estimate'] is not None for d in rows),
                                 'rejection_reasons': dict(Counter(r for d in rows for r in d['reasons']))}
    with sqlite3.connect(f"file:{home / 'data/discovery.sqlite3'}?mode=ro", uri=True) as db:
        assert db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        for raw, sha in db.execute('SELECT payload,sha256 FROM discovery_payloads'):
            assert hashlib.sha256(raw).hexdigest() == sha
            assert json.loads(gzip.decompress(raw))['fills'] == 0
    return {'run_id': p['id'], 'started': p['ts'], 'finished': p['finished'], 'health': p['health'],
            'source_quality': source_quality, 'strategies': by_strategy, 'scenarios_brl': p['scenarios_brl'],
            'observed_pairs': len(p['samples']), 'decisions': len(p['decisions']), 'real_fills': 0, 'real_pnl': 0,
            'correlated_observations_not_independent': True, 'fx': p['fx'], 'model_source_sha256': p['model_source_sha256']}


def security_gate():
    forbidden = {'create_order', 'create_orders', 'cancel_order', 'withdraw', 'transfer', 'deposit',
                 'set_leverage', 'set_margin_mode', 'fetch_balance', 'fetch_positions'}
    for path in (ROOT / 'robo').glob('discovery*.py'):
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, (path, node.lineno)
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                assert 'tests' not in ast.unparse(node)
    return 'PASS: fixed GET capability list, no account/order/transfer calls, no test imports; Ruff S and hostile transport tests separately executed'


def main():
    report = {'started': time.time(), 'checks': [], 'security': security_gate()}
    commands = [
        ['.venv/bin/pytest', '-q'], ['.venv/bin/ruff', 'check', 'robo', 'scripts', 'tests'],
        ['.venv/bin/python', '-m', 'compileall', '-q', 'robo', 'scripts', 'tests'],
        *[['node', '--check', name] for name in ('web/app.js', 'web/phase2.js', 'web/discovery.js', 'scripts/expansion-browser.cjs')],
        ['.venv/bin/python', 'scripts/expansion-preservation.py'],
    ]
    try:
        for command in commands:
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=180, check=False)  # noqa: S603 — fixed commands
            report['checks'].append({'command': command, 'exit_code': result.returncode, 'stdout': result.stdout, 'stderr': result.stderr})
            assert result.returncode == 0, command
        report['real_capture'] = capture_audit()
        report['browser'] = json.loads((ROOT / 'evidence/expansion-browser.json').read_text())
        assert report['browser']['status'] == 'PASS'
        report['status'] = 'PASS'
    except Exception as exc:
        report['status'] = 'FAIL'
        report['failure'] = str(exc)
        raise
    finally:
        report['finished'] = time.time()
        report['verifier_peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        (ROOT / 'evidence/expansion-gates.json').write_text(json.dumps(report, indent=2))
        print(json.dumps({k: v for k, v in report.items() if k not in {'checks', 'browser', 'real_capture'}}, indent=2))


if __name__ == '__main__':
    main()

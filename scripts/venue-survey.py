"""Resumable inventory of ALL installed CCXT IDs, one isolated worker, public GET only."""
import argparse
import asyncio
import hashlib
import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import ccxt
from robo.venue_survey import probe, SURVEY_BUDGET

ROOT = Path(__file__).resolve().parents[1]
DIRECTORY = ROOT / 'data/venue-survey'
REPORTS = ROOT / 'evidence/venue-survey'


def save(path, value):
    raw = json.dumps(value, indent=2, allow_nan=False)
    temporary = path.with_suffix('.part')
    temporary.write_text(raw)
    temporary.chmod(0o600)
    temporary.replace(path)


def inventory():
    ids = list(ccxt.exchanges)
    records = []
    for venue in ids:
        e = getattr(ccxt, venue)()
        records.append({'id': venue, 'name': e.name, 'countries': e.countries,
                        'capabilities': {k: e.has.get(k) for k in ('fetchMarkets', 'fetchOrderBook', 'fetchFundingRate')},
                        'rate_limit_ms': e.rateLimit, 'required_credentials_flags': e.requiredCredentials,
                        'credentials': 'NONE_READ_OR_CONFIGURED'})
    value = {'ccxt_version': ccxt.__version__, 'total': len(ids), 'ids': ids, 'providers': records,
             'scope': 'ALL_INSTALLED_IDS, NO_REAL_OPERATIONS'}
    save(REPORTS / 'inventory.json', value)
    return ids


def checkpoint(ids):
    results = []
    hashes = {}
    for venue in ids:
        path = REPORTS / (venue + '.json')
        if path.exists():
            raw = path.read_bytes()
            value = json.loads(raw)
            assert value['id'] == venue and value['ccxt_version'] == ccxt.__version__
            results.append(value)
            hashes[venue] = hashlib.sha256(raw).hexdigest()
    out = {'at': time.time(), 'ccxt_version': ccxt.__version__, 'installed': len(ids),
           'tested': len(results), 'remaining': [v for v in ids if v not in hashes],
           'statuses': dict(Counter(r['status'] for r in results)),
           'reasons': dict(Counter(reason for r in results for reason in r['reasons'])),
           'new_continuous_admissions': 0, 'existing_continuous_research_sources': ['bybit', 'bitget', 'okx', 'kucoin', 'binance', 'bingx', 'gate'],
           'http_requests': sum(r.get('request_count', 0) for r in results),
           'report_sha256': hashes, 'live': False, 'real_fills': 0, 'real_pnl': 0,
           'resumption': 'PYTHONPATH=. .venv/bin/python scripts/venue-survey.py --batch 104 (skips saved exact-version provider reports)'}
    save(REPORTS / 'checkpoint.json', out)
    return out


def child(venue):
    assert venue in ccxt.exchanges
    os.nice(10)
    # CPU/wall/memory isolated from production. One child at a time, no automatic installs.
    resource.setrlimit(resource.RLIMIT_CPU, (20, 25))
    resource.setrlimit(resource.RLIMIT_AS, (640 * 1024 * 1024, 640 * 1024 * 1024))
    start = time.monotonic()
    result = asyncio.run(asyncio.wait_for(probe(venue, DIRECTORY), 32))
    result['elapsed_s'] = round(time.monotonic() - start, 3)
    result['peak_rss_kib'] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    save(REPORTS / (venue + '.json'), result)
    print(venue, result['status'], result['request_count'], ','.join(result['reasons']), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--one', choices=ccxt.exchanges)
    parser.add_argument('--batch', type=int, default=10)
    args = parser.parse_args()
    DIRECTORY.mkdir(parents=True, exist_ok=True, mode=0o700)
    REPORTS.mkdir(parents=True, exist_ok=True, mode=0o700)
    if args.one:
        child(args.one)
        return
    ids = inventory()
    completed = 0
    for venue in ids:
        if (REPORTS / (venue + '.json')).exists():
            continue
        if completed >= args.batch:
            break
        used = sum(p.stat().st_size for p in DIRECTORY.glob('*.gz'))
        if used >= SURVEY_BUDGET - 8 * 1024 * 1024:
            print('SURVEY_DISK_QUOTA_CHECKPOINT', flush=True)
            break
        try:
            result = subprocess.run([sys.executable, __file__, '--one', venue], cwd=ROOT, timeout=45,  # noqa: S603 — fixed scoped script, enum ID
                                    capture_output=True, text=True, check=False)
        except subprocess.TimeoutExpired:
            save(REPORTS / (venue + '.json'), {'id': venue, 'ccxt_version': ccxt.__version__,
                 'status': 'REJECTED', 'reasons': ['ISOLATED_WORKER_WALL_TIMEOUT'],
                 'continuous_admission': False, 'live': False, 'fills': 0})
            completed += 1
            checkpoint(ids)
            continue
        if result.returncode:
            save(REPORTS / (venue + '.json'), {'id': venue, 'ccxt_version': ccxt.__version__,
                 'status': 'REJECTED', 'reasons': ['ISOLATED_WORKER_FAILURE'], 'exit_code': result.returncode,
                 'diagnostic': result.stderr[-1200:], 'continuous_admission': False, 'live': False, 'fills': 0})
        print(result.stdout.strip() or venue + ' worker exit=' + str(result.returncode), flush=True)
        completed += 1
        current = checkpoint(ids)
        print('CHECKPOINT', current['tested'], '/', current['installed'], current['statuses'], flush=True)
    print(json.dumps(checkpoint(ids), indent=2))


if __name__ == '__main__':
    main()

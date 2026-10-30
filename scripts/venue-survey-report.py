"""Validate all survey files/raw hashes and publish an honest per-ID admission matrix."""
import gzip
import hashlib
import json
import math

from pathlib import Path
import ccxt
from robo.discovery import normalized_book

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / 'evidence/venue-survey'
RAW = ROOT / 'data/venue-survey'


def contains_number(value, target):
    if isinstance(value, dict):
        return any(contains_number(v, target) for v in value.values())
    if isinstance(value, list):
        return any(contains_number(v, target) for v in value)
    if isinstance(value, bool) or value is None:
        return False
    try:
        number = float(value)
        return math.isfinite(number) and any(abs(number - t) < 0.01 for t in (target, target / 1000))
    except (ValueError, TypeError, OverflowError):
        return False


def quality_sample(sample, bodies):
    reasons = list(sample.get('reasons', []))
    m = sample['market']
    book = sample.get('book')
    if not book:
        return sorted(set(reasons + ['NO_L2']))
    cs = m.get('contractSize') if m.get('swap') else 1
    try:
        normalized_book(book, cs)
    except (ValueError, TypeError, KeyError, IndexError):
        reasons.append('INVALID_L2_OR_CONTRACT_UNITS')
    ts = book.get('timestamp')
    if not ts or not any(contains_number(body, ts) for body in bodies):
        reasons.append('BOOK_TIMESTAMP_NOT_PROVEN_IN_RAW')
    if sample.get('precision_mode') != ccxt.TICK_SIZE or not (m.get('precision') or {}).get('amount'):
        reasons.append('AMOUNT_PRECISION_UNVERIFIED')
    for kind in ('amount', 'cost'):
        minimum = ((m.get('limits') or {}).get(kind) or {}).get('min')
        if minimum is None:
            reasons.append('MIN_' + kind.upper() + '_UNVERIFIED')
    if m.get('quote') != 'USDT' or (m.get('swap') and m.get('settle') != 'USDT'):
        reasons.append('QUOTE_OR_SETTLE_NOT_USDT_NOT_ASSUMED_EQUIVALENT')
    if m.get('swap'):
        f = sample.get('funding') or {}
        ft = f.get('source_asof')
        if not ft or not any(contains_number(body, ft) for body in bodies):
            reasons.append('FUNDING_ASOF_NOT_PROVEN_IN_RAW')
        if not f.get('interval_h'):
            reasons.append('FUNDING_INTERVAL_UNVERIFIED')
    return sorted(set(reasons))


def admission(samples, existing=False):
    """New unified funding adapter requires both qualified legs, never a lone ticker/book."""
    spot = [s for s in samples if s['kind'] == 'spot' and s['snapshot_data_gate'] == 'PASS_AT_CAPTURE']
    perp = [s for s in samples if s['kind'] == 'linear_perp' and s['snapshot_data_gate'] == 'PASS_AT_CAPTURE']
    reasons = []
    if not spot:
        reasons.append('NO_FULLY_QUALIFIED_SPOT_SAMPLE')
    if not perp:
        reasons.append('NO_FULLY_QUALIFIED_LINEAR_PERP_SAMPLE')
    if spot and perp and not any(a['base'] == b['base'] and abs(a['timestamp'] - b['timestamp']) <= 3000 for a in spot for b in perp):
        reasons.append('NO_MATCHED_SYNCHRONIZED_QUALIFIED_PAIR')
    # Spot-only B or perp-only C never turns missing inventory/rebalance/margin into proof.
    if not existing:
        reasons.extend(['NO_AUDITED_FIXED_CONTINUOUS_ADAPTER', 'LONGITUDINAL_RELIABILITY_NOT_PROVEN',
                        'B_INVENTORY_REBALANCE_AND_C_MARGIN_NOT_QUALIFIED'])
    return reasons


def main():
    inventory = json.loads((REPORTS / 'inventory.json').read_text())
    checkpoint = json.loads((REPORTS / 'checkpoint.json').read_text())
    assert inventory['ids'] == ccxt.exchanges and inventory['total'] == len(ccxt.exchanges) == 104
    assert checkpoint['tested'] == 104 and not checkpoint['remaining']
    entries = []
    verified_raw = set()
    for venue in inventory['ids']:
        path = REPORTS / (venue + '.json')
        raw = path.read_bytes()
        assert hashlib.sha256(raw).hexdigest() == checkpoint['report_sha256'][venue]
        p = json.loads(raw)
        assert p['id'] == venue and p['ccxt_version'] == ccxt.__version__
        assert not p['continuous_admission'] and not p['live'] and p['fills'] == 0
        assert len(p.get('http', [])) <= 16
        bodies = []
        for call in p.get('http', []):
            assert call['method'] == 'GET'
            if call.get('sha256'):
                saved = RAW / call['evidence']
                body = gzip.decompress(saved.read_bytes())
                assert hashlib.sha256(body).hexdigest() == call['sha256']
                assert len(body) == call['bytes']
                bodies.append(json.loads(body))
                verified_raw.add(saved.name)
        samples = []
        for sample in p.get('samples', []):
            reasons = quality_sample(sample, bodies)
            samples.append({'symbol': sample['symbol'], 'base': sample['market']['base'],
                            'kind': 'spot' if sample['market']['spot'] else 'linear_perp', 'quality_reasons': reasons,
                            'snapshot_data_gate': 'PASS_AT_CAPTURE' if not reasons else 'REJECT',
                            'timestamp': (sample.get('book') or {}).get('timestamp')})
        promotion = admission(samples, venue in {'bybit','bitget','okx','kucoin','binance','bingx','gate'})
        entries.append({'id': venue, 'status': p['status'], 'network_requests': p.get('request_count', 0),
                        'successful_http': sum(c.get('http_status') == 200 for c in p.get('http', [])),
                        'reasons': p['reasons'], 'denied_descriptors': p.get('denied', []), 'samples': samples,
                        'continuous_admission': False, 'promotion_reasons': promotion,
                        'elapsed_s': p.get('elapsed_s'), 'peak_rss_kib': p.get('peak_rss_kib')})
    stats = {'ids': len(entries), 'network_attempted_ids': sum(e['network_requests'] > 0 for e in entries),
             'network_not_attempted_fail_closed': sum(e['network_requests'] == 0 for e in entries),
             'observed_research_only': sum(e['status'] == 'OBSERVED_RESEARCH_ONLY' for e in entries),
             'rejected_providers': sum(e['status'] == 'REJECTED' for e in entries),
             'http_requests': sum(e['network_requests'] for e in entries), 'verified_raw_files': len(verified_raw),
             'snapshot_quality_pass_samples': sum(s['snapshot_data_gate'] == 'PASS_AT_CAPTURE' for e in entries for s in e['samples']),
             'new_continuous_admissions': 0,
             'peak_worker_rss_kib': max(e['peak_rss_kib'] or 0 for e in entries),
             'raw_disk_bytes': sum(p.stat().st_size for p in RAW.glob('*.gz'))}
    assert stats['ids'] == stats['network_attempted_ids'] + stats['network_not_attempted_fail_closed']
    assert stats['ids'] == stats['observed_research_only'] + stats['rejected_providers']
    report = {'status': 'PASS', 'ccxt_version': ccxt.__version__, 'statistics': stats, 'providers': entries,
              'continuous_research_approved_preexisting': ['bybit','bitget','okx','kucoin','binance','bingx','gate'],
              'notice': '104 IDs are not 104 independent businesses. Snapshot gate is not continuous admission or proof of executable profit. Unknown namespace/POST/auth fail closed. Only fixed existing adapters run continuously.'}
    (REPORTS / 'validated-summary.json').write_text(json.dumps(report, indent=2))
    lines = ['AUDITORIA DOS 104 IDs CCXT — DADOS PÚBLICOS, SOMENTE PESQUISA', '',
             json.dumps(stats, ensure_ascii=False, indent=2), '', report['notice'], '',
             'ID | HTTP requests | resultado do probe | impedimentos de qualidade/promoção']
    for e in entries:
        reasons = sorted(set(e['reasons'] + e['promotion_reasons'] + [r for s in e['samples'] for r in s['quality_reasons']]))
        lines.append(f"{e['id']} | {e['network_requests']} | {e['status']} | {', '.join(reasons)}")
    (ROOT / 'docs/EXPANSAO_CCXT_104_AUDITORIA.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps(stats, indent=2))


if __name__ == '__main__':
    main()

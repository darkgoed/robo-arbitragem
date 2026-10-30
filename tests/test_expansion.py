"""SYNTHETIC FIXTURES ONLY: pure fail-closed QA. Never used by scanner/dashboard."""
import asyncio
import copy

import sqlite3

import httpx
import pytest
from robo.discovery import evaluate, normalized_book
from robo.discovery_store import setup, record, store_payload, detail, history, compact, connect
from robo.discovery_transport import public_only, PublicTransport
from robo.discovery_strategies import cross, funding_fields
from robo.discovery_scanner import Scanner, scenarios
from test_api import client as client, login

TS = 1_790_000_000_000


def sample(venue='okx', rate=0.0001, interval=8):
    market = {'active': True, 'linear': True, 'taker': 0.001, 'contractSize': 0.01,
              'precision': {'amount': 1}, 'limits': {'amount': {'min': 1}, 'cost': {'min': 5}}}
    spot_market = copy.deepcopy(market)
    spot_market.update(contractSize=None, precision={'amount': 0.001})
    spot_market['limits']['amount']['min'] = 0.001
    return {'venue': venue, 'base': 'BTC', 'precision_mode': 4,
            'spot': {'bids': [[100, 1000]], 'asks': [[100.1, 1000]], 'timestamp': TS},
            'perp': {'bids': [[100.2, 100000]], 'asks': [[100.3, 100000]], 'timestamp': TS},
            'spot_market': spot_market, 'perp_market': market,
            'funding': {'fundingRate': rate, 'source_asof': TS, 'timestamp': TS,
                        'fundingTimestamp': TS + 3600000, 'interval_h': interval}}


def model(s=None, budget=1000, fx=None):
    s = s or sample()
    return evaluate(s['spot'], s['perp'], s['funding'], s['spot_market'], s['perp_market'], 4,
                    fx or {'usd_brl': 5, 'asof': TS}, TS, budget)


@pytest.mark.parametrize('suffix', ['/extra', '%2fprivate', '/../account', '?signature=x', '?apiKey=x', '?recvWindow=5000', '#frag'])
def test_exact_http_allowlist(suffix):
    assert not public_only('okx', 'https://www.okx.com/api/v5/market/books' + suffix)


@pytest.mark.parametrize('url', [
    'https://evil.test/api/v5/market/books', 'https://www.okx.com:443/api/v5/market/books',
    'https://user:password@www.okx.com/api/v5/market/books', 'https://www.okx.com:abc/api/v5/market/books',
    'http://www.okx.com/api/v5/market/books', 'https://www.okx.com/api/v5/trade/order',
])
def test_transport_no_credential_port_scheme_private(url):
    assert not public_only('okx', url)


@pytest.mark.parametrize('verb', ['POST', 'PUT', 'DELETE', 'PATCH', 'HEAD'])
def test_no_mutating_or_non_get_methods(verb):
    assert not public_only('okx', 'https://www.okx.com/api/v5/market/books', verb)


async def test_transport_denies_redirect_and_does_not_forward_credentials():
    t = PublicTransport('okx')
    await t.client.aclose()
    calls = []
    def handler(request):
        calls.append(str(request.url))
        assert 'authorization' not in request.headers
        return httpx.Response(302, headers={'Location': 'https://evil.test/'})
    t.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False, trust_env=False)
    try:
        with pytest.raises(PermissionError, match='REDIRECT_DENIED'):
            await t.fetch('https://www.okx.com/api/v5/market/books')
        with pytest.raises(PermissionError, match='NONPUBLIC'):
            await t.fetch('https://www.okx.com/api/v5/account/balance')
        assert len(calls) == 1
    finally:
        await t.close()


@pytest.mark.parametrize('status', [429, 500, 503])
async def test_transport_rate_limits_blackout_fail_closed(status):
    t = PublicTransport('okx')
    await t.client.aclose()
    t.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(status)), trust_env=False)
    try:
        with pytest.raises(httpx.HTTPStatusError):
            await t.fetch('https://www.okx.com/api/v5/market/books')
        assert t.calls[0]['http_status'] == status and 'error' in t.calls[0]
    finally:
        await t.close()


@pytest.mark.parametrize(('field', 'value', 'reason'), [
    ('timestamp', None, 'MISSING_EXCHANGE_BOOK_TIMESTAMP'),
    ('timestamp', TS - 15001, 'STALE_OR_FUTURE_BOOK'),
    ('timestamp', TS + 2001, 'STALE_OR_FUTURE_BOOK'),
    ('timestamp', TS - 4000, 'UNSYNCED_BOOKS'),
])
def test_quality_no_future_missing_stale_desync(field, value, reason):
    s = sample()
    s['spot'][field] = value
    r = model(s)
    assert r['status'] == 'REJECT' and reason in r['reasons'] and r['fills'] == 0


def test_unknown_fee_funding_negative_fx_and_contract_rejected():
    s = sample()
    s['spot_market']['taker'] = None
    assert 'FEE_METADATA_MISSING' in model(s)['reasons']
    s = sample()
    s['funding']['fundingRate'] = -0.01
    assert 'FUNDING_NONPOSITIVE' in model(s)['reasons']
    assert 'FX_UNAVAILABLE' in model(fx={'usd_brl': 5, 'asof': TS - 48 * 3600000})['reasons']
    s = sample()
    s['perp_market']['contractSize'] = None
    assert 'CONTRACT_SIZE_UNKNOWN' in model(s)['reasons']


@pytest.mark.parametrize('cs', [0, -1, float('nan'), float('inf')])
def test_invalid_contract_multiplier(cs):
    with pytest.raises(ValueError):
        normalized_book(sample()['perp'], cs)


def test_lots_limits_depth_and_capital_scenarios():
    s = sample()
    assert model(s, 5)['status'] == 'REJECT'
    s['perp_market']['limits']['amount']['max'] = 1
    assert 'MAX_AMOUNT_EXCEEDED' in model(s)['reasons']
    s = sample()
    s['spot']['asks'][0][1] = 0.000001
    assert model(s)['status'] == 'REJECT'
    for budget in (100, 1000, 5000):
        r = model(budget=budget)
        assert r['estimate']['capital_required_brl'] <= budget and r['fills'] == 0
        assert r['estimate']['perp_contract_quantity'] * 0.01 == pytest.approx(r['estimate']['base_quantity'])


def test_cross_gross_edge_does_not_prove_viability():
    a, b = sample(), sample('kucoin')
    b['spot']['bids'] = [[102, 1000]]
    b['spot']['asks'] = [[102.1, 1000]]
    r = cross(a, b, 'B', 1000, {'usd_brl': 5, 'asof': TS}, TS)
    assert r['estimate']['gross_spread_usdt'] > 0 and r['estimate']['net_before_unknown_rebalance_usdt'] > 0
    assert r['status'] == 'REJECT'
    assert 'PREFUNDED_INVENTORY_UNVERIFIED' in r['reasons'] and 'REBALANCE_COST_UNKNOWN' in r['reasons']
    assert r['estimate']['capital_required_brl'] <= 1000 and r['fills'] == 0


def test_cross_perp_different_schedules_signs_and_change():
    a, b = sample(rate=-0.001, interval=4), sample('kucoin', rate=0.002, interval=8)
    fx = {'usd_brl': 5, 'asof': TS}
    r = cross(a, b, 'C', 1000, fx, TS)
    assert r['estimate']['funding_haircut_24h_usdt'] > 0
    assert r['status'] == 'REJECT' and 'MARGIN_LIQUIDATION_RULES_UNVERIFIED' in r['reasons']
    b['funding']['interval_change'] = True
    assert 'FUNDING_SCHEDULE_UNVERIFIED' in cross(a, b, 'C', 1000, fx, TS)['reasons']
    a['funding']['fundingRate'] = 0.001
    b['funding']['fundingRate'] = -0.002
    b['funding']['interval_change'] = False
    assert cross(a, b, 'C', 1000, fx, TS)['estimate']['funding_haircut_24h_usdt'] < 0


def test_funding_times_are_never_manufactured_or_local_timezone():
    f = {'timestamp': None, 'info': {'timePoint': TS}, 'fundingTimestamp': TS + 1000}
    assert funding_fields('kucoin', f)['source_asof'] is None
    f['info'] = {'ts': str(TS), 'fundingTime': str(TS + 3600000), 'nextFundingTime': str(TS + 18000000)}
    n = funding_fields('okx', f)
    assert n['source_asof'] == TS and n['interval_h'] == 4
    assert funding_fields('bingx', {'timestamp': 1, 'info': {'updateTime': TS}})['source_asof'] == TS


def insert_run(home, rid='a' * 32, ts=TS):
    path = setup(home)
    with sqlite3.connect(path) as db:
        db.execute('INSERT INTO discovery_runs VALUES(?,?,?,?,?,?)', (rid, ts, ts + 1, 1, 1, 0))
    value = {'id': rid, 'samples': [{'label': 'SYNTHETIC_TEST_ONLY'}], 'fills': 0}
    record(path, rid, 'okx', 'BTC', {'result': {'status': 'REJECT', 'fills': 0}})
    store_payload(path, rid, value)
    return path, value


def test_hot_cold_backup_restore_checksum_and_idempotence(tmp_path):
    path, value = insert_run(tmp_path)
    record(path, value['id'], 'okx', 'BTC', {'result': {'status': 'REJECT', 'fills': 0}})
    with pytest.raises(ValueError, match='IDEMPOTENCY'):
        record(path, value['id'], 'okx', 'BTC', {'result': {'status': 'HYPOTHESIS'}})
    assert detail(tmp_path, value['id']) == value
    compact(path, keep=0)
    assert history(tmp_path)[0]['cold'] is True and detail(tmp_path, value['id']) == value
    backup = path.parent / 'restored.sqlite3'
    with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
        source.backup(target)
        assert target.execute('PRAGMA integrity_check').fetchone()[0] == 'ok'
        assert target.execute('SELECT COUNT(*) FROM discovery_archives').fetchone()[0] == 1
    archive = next((path.parent / 'discovery-archives').glob('*.gz'))
    archive.write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='CHECKSUM'):
        detail(tmp_path, value['id'])


def test_quota_never_deletes_originals(tmp_path):
    path, value = insert_run(tmp_path)
    with pytest.raises(ValueError, match='BUDGET_BLOCKED'):
        compact(path, keep=0, budget=0)
    assert detail(tmp_path, value['id']) == value
    assert history(tmp_path)[0]['cold'] is False


def test_sqlite_quota_is_set_on_every_writer_connection(tmp_path):
    path = setup(tmp_path)
    for _ in range(2):
        with connect(path) as db:
            assert db.execute('PRAGMA max_page_count').fetchone()[0] == 16384
            assert db.execute('PRAGMA journal_size_limit').fetchone()[0] == 4194304


async def test_scanner_overlap_and_outage_no_network(tmp_path, monkeypatch):
    scanner = Scanner(tmp_path)
    scanner.running = True
    with pytest.raises(ValueError, match='ALREADY_RUNNING'):
        await scanner.cycle()
    scanner.running = False
    async def unavailable(*args, **kwargs):
        raise TimeoutError('SYNTHETIC_TEST_OUTAGE')
    import ccxt.async_support as ccxt
    monkeypatch.setattr(ccxt.Exchange, 'load_markets', unavailable)
    import robo.discovery_scanner as module
    monkeypatch.setattr(module, 'load_binance', unavailable)
    p = await scanner.cycle()
    assert len(p['samples']) == 40 and len(p['decisions']) == 1080
    assert all(d['status'] == 'REJECT' and d['fills'] == 0 for d in p['decisions'])
    assert all(h['state'] == 'UNAVAILABLE' for h in p['health'].values())
    assert all(scanner.retry_after[v] > 0 for v in scanner.retry_after)
    assert scanner.running is False


async def test_scanner_cancellation_cleanup(tmp_path, monkeypatch):
    import ccxt.async_support as ccxt
    entered = asyncio.Event()
    async def stall(*args, **kwargs):
        entered.set()
        await asyncio.sleep(60)
    monkeypatch.setattr(ccxt.Exchange, 'load_markets', stall)
    scanner = Scanner(tmp_path)
    task = asyncio.create_task(scanner.cycle())
    await asyncio.wait_for(entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert scanner.running is False


def test_private_api_config_csrf_isolation_and_cold_corruption(client):
    for url in ('/api/discovery', '/api/discovery/history', '/api/discovery/history/' + 'a' * 32):
        assert client.get(url).status_code == 401
    login(client)
    before = client.app.state.runtime.paper.state()
    assert client.get('/api/discovery').json()['live'] is False
    payload = {'budgets_brl': [100, 1000, 5000]}
    assert client.post('/api/discovery/scenarios', json=payload, headers={'X-CSRF-Token': 'wrong'}).status_code == 403
    assert client.post('/api/discovery/scenarios', json=payload | {'live': True}).status_code == 422
    assert client.post('/api/discovery/scenarios', json={'budgets_brl': [100, 100]}).status_code == 409
    assert client.post('/api/discovery/scenarios', json=payload).json()['auto_open'] is False
    home = client.app.state.store.directory.parent
    assert Scanner(home).budgets == (100, 1000, 5000)
    assert client.app.state.runtime.paper.state() == before
    path, _ = insert_run(home)
    compact(path, keep=0)
    assert client.get('/api/discovery/history').json()['runs'][0]['cold'] is True
    assert client.get('/api/discovery/history/' + 'a' * 32).status_code == 200
    with sqlite3.connect(path) as db:
        db.execute("UPDATE discovery_archives SET sha256='bad'")
    assert client.get('/api/discovery/history/' + 'a' * 32).status_code == 503
    assert client.get('/health/live').status_code == 200
    assert client.get('/api/discovery/history/not-a-run').status_code == 422
    assert client.get('/api/discovery/history?limit=99999').status_code == 422


@pytest.mark.parametrize('values', [[], [1], [float('nan')], [float('inf')], [100, 100], [1, 2, 3, 4, 5, 6], [10001]])
def test_configuration_validation(values):
    with pytest.raises(ValueError):
        scenarios(values)

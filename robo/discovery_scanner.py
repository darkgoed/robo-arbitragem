"""Bounded parallel research worker. Never modifies legacy ledger or exchange accounts."""
import asyncio
import hashlib
import fcntl
import json
import logging
import sqlite3
import time
import uuid
from pathlib import Path
from itertools import permutations

import ccxt.async_support as ccxt

from robo.discovery import BASES, VENUES, evaluate, public_fx
from robo.discovery_store import setup, record, store_payload, compact, history, detail, connect, BUDGET
from robo.discovery_strategies import cross, funding_fields
from robo.discovery_transport import PublicTransport
from robo.store import now_ms

log = logging.getLogger('robo')
DEFAULT_SCENARIOS = (100, 1000, 5000)


def scenarios(values):
    values = tuple(values)
    if not 1 <= len(values) <= 5 or len(set(values)) != len(values):
        raise ValueError('SCENARIO_COUNT_OR_DUPLICATES')
    if any(not isinstance(v, (int, float)) or not 5 <= v <= 10000 for v in values):
        raise ValueError('SCENARIO_BUDGET_RANGE')
    return values


def clean_market(market):
    return {k: market.get(k) for k in ('id', 'symbol', 'active', 'spot', 'swap', 'linear', 'quote', 'settle',
                                     'taker', 'contractSize', 'limits', 'precision', 'info')}


def clean_book(book):
    return {k: book.get(k) for k in ('timestamp', 'bids', 'asks', 'nonce')}


async def load_binance(exchange):
    # Official spot symbols filter avoids downloading the entire multi-MB catalogue.
    spot = await exchange.publicGetExchangeInfo({'symbols': json.dumps([b + 'USDT' for b in BASES], separators=(',', ':'))})
    perp = await exchange.fapiPublicGetExchangeInfo()
    raw = spot['symbols'] + [m for m in perp['symbols'] if m.get('symbol') in {b + 'USDT' for b in BASES} and m.get('contractType') == 'PERPETUAL']
    exchange.options['crossMarginPairsData'] = []
    exchange.options['isolatedMarginPairsData'] = []
    exchange.set_markets(exchange.parse_markets(raw))


class Scanner:
    def __init__(self, home, budgets=DEFAULT_SCENARIOS):
        self.home = Path(home)
        self.budgets = scenarios(budgets)
        self.failures = {v: 0 for v in VENUES}
        self.retry_after = {v: 0 for v in VENUES}
        self.running = False
        self.last_error = None
        self.last_cycle = None
        model_files = ('discovery.py', 'discovery_scanner.py', 'discovery_store.py',
                       'discovery_strategies.py', 'discovery_transport.py', 'engine.py')
        source = hashlib.sha256()
        for name in model_files:
            source.update(name.encode())
            source.update(Path(__file__).with_name(name).read_bytes())
        self.code_sha256 = source.hexdigest()

        config = self.home / 'data/discovery-config.json'
        if config.exists():
            try:
                self.budgets = scenarios(json.loads(config.read_text())['budgets_brl'])
            except (ValueError, KeyError, OSError):
                self.last_error = 'INVALID_CONFIG_DEFAULTS_USED'

    def configure(self, values):
        values = scenarios(values)
        path = self.home / 'data/discovery-config.json'
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = path.with_suffix('.part')
        temporary.write_text(json.dumps({'budgets_brl': values, 'live': False, 'auto_open': False}))
        temporary.chmod(0o600)
        temporary.replace(path)
        self.budgets = values

    async def cycle(self):
        if self.running:
            raise ValueError('DISCOVERY_CYCLE_ALREADY_RUNNING')
        exchanges, transports = {}, {}
        path = setup(self.home)
        lock = (path.parent / 'discovery.lock').open('a')
        self.running = True
        budgets = self.budgets
        rid = uuid.uuid4().hex
        started = now_ms()
        fx = public_fx(self.home)
        samples, decisions, health = [], [], {}
        try:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('DISCOVERY_CYCLE_ALREADY_RUNNING') from None
            archive = await asyncio.to_thread(compact, path)
            if archive['archive_bytes'] > BUDGET - 2 * 1024 * 1024:
                raise ValueError('DISCOVERY_ARCHIVE_BUDGET_BLOCKED')
            with connect(path) as db:
                db.execute('INSERT INTO discovery_runs VALUES(?,?,?,?,?,?)', (rid, started, None, len(VENUES) * len(BASES), 0, 0))
            for venue in VENUES:
                health[venue] = {'state': 'UNAVAILABLE', 'requests': 0, 'errors': 0, 'pairs': 0}
                if time.monotonic() < self.retry_after[venue]:
                    health[venue]['error'] = 'BACKOFF'
                    continue
                transport = PublicTransport(venue)
                transports[venue] = transport
                options = {'fetchCurrencies': False, 'adjustForTimeDifference': False}
                if venue in {'okx', 'gate'}:
                    options['fetchMarkets'] = {'types': ['spot', 'swap']}
                if venue == 'binance':
                    options['fetchMarkets'] = {'types': ['spot', 'linear']}
                if venue == 'gate':
                    options['swap'] = {'fetchMarkets': {'settlementCurrencies': ['usdt']}}
                exchange = getattr(ccxt, venue)({'enableRateLimit': True, 'timeout': 6500, 'options': options})
                exchange.has['fetchCurrencies'] = False
                # No API keys, environment proxy, CCXT fetch or redirect fallback.
                exchange.fetch = transport.fetch
                exchanges[venue] = exchange
                try:
                    if venue == 'binance':
                        await asyncio.wait_for(load_binance(exchange), 22)
                    else:
                        await asyncio.wait_for(exchange.load_markets(), 22)
                    health[venue]['state'] = 'METADATA_READY'
                except Exception as exc:
                    health[venue]['error'] = type(exc).__name__
                    self.failures[venue] += 1
                    self.retry_after[venue] = time.monotonic() + min(3600, 60 * 2 ** min(6, self.failures[venue]))
            # All venues for the SAME base are acquired within one bounded wave.
            for base in BASES:
                wave = await asyncio.gather(*(self.sample(v, base, exchanges.get(v), health[v]) for v in VENUES))
                ts = now_ms()
                for sample in wave:
                    samples.append(sample)
                    for budget in budgets:
                        if sample.get('spot') and sample.get('perp'):
                            result = evaluate(sample['spot'], sample['perp'], sample['funding'], sample['spot_market'],
                                              sample['perp_market'], sample['precision_mode'], fx, ts, budget)
                        else:
                            result = {'status': 'REJECT', 'reasons': [sample.get('error', 'LEG_DATA_UNAVAILABLE')], 'estimate': None, 'fills': 0}
                        decision = result | {'strategy': 'A', 'venue': sample['venue'], 'base': base, 'budget_brl': budget,
                                             'baseline_no_trade_pnl': 0, 'real_pnl': 0}
                        decisions.append(decision)
                    record(path, rid, sample['venue'], base, sample | {'result': decisions[-len(budgets)]})
                for a, b in permutations(wave, 2):
                    for strategy in ('B', 'C'):
                        for budget in budgets:
                            decisions.append(cross(a, b, strategy, budget, fx, ts))
            for venue, transport in transports.items():
                calls = transport.calls
                health[venue].update(requests=len(calls), errors=sum('error' in c for c in calls),
                                     latency_max_ms=max((c.get('latency_ms', 0) for c in calls), default=0),
                                     latency_mean_ms=round(sum(c.get('latency_ms', 0) for c in calls) / max(1, len(calls))))
                if health[venue]['pairs'] == len(BASES):
                    health[venue]['state'] = 'OBSERVED'
                    self.failures[venue] = 0
                elif health[venue]['pairs']:
                    health[venue]['state'] = 'DEGRADED'
                else:
                    health[venue]['state'] = 'UNAVAILABLE'
            payload = {'id': rid, 'ts': started, 'finished': now_ms(), 'fx': fx, 'samples': samples,
                       'decisions': decisions, 'health': health, 'http': {v: t.calls for v, t in transports.items()},
                       'scenarios_brl': budgets, 'archive': archive, 'origin': 'OBSERVED_PUBLIC_REST',
                       'auto_open': False, 'live': False, 'fills': 0, 'real_pnl': 0,
                       'baseline': 'NO_TRADE_ZERO_PNL', 'model_version': 'expansion-v1',
                       'model_source_sha256': self.code_sha256}
            # Read-back is an independent hash + decompress, before marking the cycle complete.
            await asyncio.to_thread(store_payload, path, rid, payload)
            assert detail(self.home, rid) == json.loads(json.dumps(payload))
            with connect(path) as db:
                db.execute('UPDATE discovery_runs SET finished=?,collected=?,hypotheses=? WHERE id=?',
                           (payload['finished'], len(samples), sum(d['status'] == 'HYPOTHESIS' for d in decisions), rid))
            self.last_cycle, self.last_error = now_ms(), None
            return payload
        finally:
            await asyncio.gather(*(t.close() for t in transports.values()), return_exceptions=True)
            await asyncio.gather(*(e.close() for e in exchanges.values()), return_exceptions=True)
            self.running = False
            lock.close()

    async def sample(self, venue, base, exchange, health):
        sample = {'venue': venue, 'base': base, 'observed_at': now_ms(), 'fills': 0}
        if exchange is None or health['state'] not in {'METADATA_READY', 'OBSERVED'} or health.get('consecutive_errors', 0) >= 3:
            return sample | {'error': health.get('error', 'VENUE_BACKOFF')}
        sm, pm = exchange.markets.get(base + '/USDT'), exchange.markets.get(base + '/USDT:USDT')
        if not sm or not pm:
            return sample | {'error': 'MARKET_PAIR_UNAVAILABLE'}
        sample.update(spot_market=clean_market(sm), perp_market=clean_market(pm), precision_mode=exchange.precisionMode)
        try:
            sp, pe, f = await asyncio.wait_for(asyncio.gather(exchange.fetch_order_book(sm['symbol'], 20),
                                                            exchange.fetch_order_book(pm['symbol'], 20),
                                                            exchange.fetch_funding_rate(pm['symbol']), return_exceptions=True), 18)
            for value in (sp, pe, f):
                if isinstance(value, BaseException):
                    raise value
            sample.update(spot=clean_book(sp), perp=clean_book(pe), funding=funding_fields(venue, f), observed_at=now_ms())
            health['pairs'] += 1
            health['consecutive_errors'] = 0
        except Exception as exc:
            sample['error'] = 'PUBLIC_FETCH_' + type(exc).__name__
            health['consecutive_errors'] = health.get('consecutive_errors', 0) + 1
        return sample

    async def run(self):
        while True:
            start = time.monotonic()
            try:
                await asyncio.wait_for(self.cycle(), 240)
            except Exception as exc:
                self.last_error = type(exc).__name__ + ': ' + str(exc)[:100]
                log.warning('discovery_cycle_failed %s', self.last_error)
            await asyncio.sleep(max(60, 900 - (time.monotonic() - start)))


def overview(home, budget=1000):
    latest = history(home, 1)
    result = {'ready': False, 'live': False, 'auto_open': False, 'real_fills': 0, 'real_pnl': 0,
              'venues': VENUES, 'bases': BASES, 'observations': [], 'decisions': [], 'scenarios_brl': DEFAULT_SCENARIOS}
    if not latest:
        return result
    payload = detail(home, latest[0]['id'])
    if payload is None:
        # Compatibility for audited pre-expansion draft rows; never silent on corruption.
        path = Path(home) / 'data/discovery.sqlite3'
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True, timeout=1) as db:
            rows = db.execute('SELECT venue,base,body,sha256 FROM discovery_observations WHERE run_id=?', (latest[0]['id'],)).fetchall()
        for venue, base, body, sha in rows:
            if hashlib.sha256(body.encode()).hexdigest() != sha:
                raise ValueError('DISCOVERY_CHECKSUM_MISMATCH')
            result['observations'].append(json.loads(body)['result'] | {'venue': venue, 'base': base})
        return result | {'ready': True, 'legacy_draft': True}
    selected = [d for d in payload['decisions'] if d['budget_brl'] == budget]
    return result | {'ready': True, 'id': payload['id'], 'asof': payload['finished'], 'health': payload['health'],
                     'age_seconds': max(0, (now_ms() - payload['finished']) / 1000), 'snapshot_expired': True,
                     'decisions': selected, 'observations': [d for d in selected if d['strategy'] == 'A'],
                     'scenarios_brl': payload['scenarios_brl'], 'budget_brl': budget, 'fx': payload['fx'],
                     'archive': payload['archive'], 'total_decisions_all_scenarios': len(payload['decisions']),
                     'notice': 'Snapshots auditáveis; não são sinais atuais, fills ou PnL. Contas hipotéticas pré-fundeadas; USDT=USD é hipótese.'}

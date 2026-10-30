"""One-shot public capability survey, never continuous admission or account access.

CCXT public API namespace + explicit GET descriptor is required BEFORE signing.
Unknown namespaces, POST-based public protocols and authenticated feeds fail closed.
"""
import asyncio
import gzip
import hashlib
import ipaddress
import json
import math

import socket
import time

from urllib.parse import parse_qsl, urlsplit

import ccxt.async_support as ccxt
import httpx

from robo.discovery import normalized_book
from robo.discovery_strategies import funding_fields, book_guard
from robo.store import now_ms

SURVEY_BUDGET = 192 * 1024 * 1024
MAX_REQUESTS = 16
FORBIDDEN = ('private', 'signed', 'account', 'wallet', 'order', 'trade', 'withdraw', 'deposit', 'transfer', 'user', 'position', 'balance', 'auth', 'broker')
# A public orderbook/trade-history path is not an order-placement capability.
SAFE_SEGMENTS = {'orderbook', 'order_book', 'order-book', 'orderbooks', 'depth', 'trades', 'trade-history', 'tradehistory'}


def public_descriptor(api, method, path, descriptor):
    parts = api if isinstance(api, list) else [api]
    if method != 'GET' or not all(isinstance(p, str) for p in parts):
        return False
    if not any('public' in p.lower() for p in parts) or any('private' in p.lower() or 'signed' in p.lower() for p in parts):
        return False
    node = descriptor
    try:
        for part in parts:
            node = node[part]
        routes = node.get('get', {})
        if path not in routes:
            return False
    except (KeyError, TypeError, AttributeError):
        return False
    import re
    segments = re.split(r'[/._-]', path.lower())
    # Fail closed on potentially stateful/account resources, permit explicit market-book names.
    scrubbed = path.lower()
    for safe in sorted(SAFE_SEGMENTS, key=len, reverse=True):
        scrubbed = scrubbed.replace(safe, 'marketdata')
    return not any(word in scrubbed for word in FORBIDDEN) and not any(s in {'me', 'my'} for s in segments)


def safe_request(request, origins):
    try:
        u = urlsplit(request['url'])
        headers = request.get('headers') or {}
        if not (request.get('method') == 'GET' and request.get('body') is None and u.scheme == 'https'
                and u.hostname in origins and u.username is None and u.password is None
                and u.port is None and not u.fragment):
            return False
        bad = ('key', 'sign', 'token', 'secret', 'auth', 'cookie', 'session', 'passphrase')
        return not any(any(w in k.lower() for w in bad) for k in headers) and not any(
            any(w in k.lower() for w in bad) for k, _ in parse_qsl(u.query, keep_blank_values=True))
    except (ValueError, TypeError, KeyError):
        return False


def api_origins(urls, hostname=None):
    if isinstance(urls, str):
        if hostname:
            urls = urls.replace('{hostname}', hostname)
        u = urlsplit(urls)
        return {u.hostname} if u.scheme == 'https' and u.hostname and '{' not in u.hostname else set()
    if isinstance(urls, dict):
        return set().union(*(api_origins(v, hostname) for v in urls.values()))
    return set()


class SurveyTransport:
    def __init__(self, exchange, directory):
        self.exchange = exchange
        self.directory = directory
        self.calls = []
        self.denied = []
        self.allowed = set()
        self.origins = api_origins(exchange.urls.get('api', {}), exchange.hostname)
        self.client = httpx.AsyncClient(timeout=5, trust_env=False, follow_redirects=False,
                                        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        self.lock = asyncio.Lock()
        self.resolved = set()
        self.last = 0
        self.sign_original = exchange.sign
        self.bytes = 0

    def sign(self, path, api='public', method='GET', params=None):
        if not public_descriptor(api, method, path, self.exchange.api):
            self.denied.append({'path': path, 'api': api, 'method': method, 'reason': 'NOT_EXPLICIT_PUBLIC_GET'})
            raise PermissionError('NOT_EXPLICIT_PUBLIC_GET')
        request = self.sign_original(path, api, method, params or {})
        if not safe_request(request, self.origins):
            self.denied.append({'path': path, 'api': api, 'method': method, 'reason': 'URL_OR_CREDENTIAL_DENIED'})
            raise PermissionError('URL_OR_CREDENTIAL_DENIED')
        # Exact signed public GET URL capability. fetch cannot create new capabilities.
        self.allowed.add(request['url'])
        return request

    async def fetch(self, url, method='GET', headers=None, body=None):
        request = {'url': url, 'method': method, 'headers': headers, 'body': body}
        if url not in self.allowed or not safe_request(request, self.origins):
            raise PermissionError('UNSIGNED_PUBLIC_CAPABILITY_DENIED')
        async with self.lock:
            if len(self.calls) >= MAX_REQUESTS or self.bytes >= 12 * 1024 * 1024:
                raise ValueError('SURVEY_REQUEST_OR_BYTE_BUDGET')
            host = urlsplit(url).hostname
            if host not in self.resolved:
                records = await asyncio.get_running_loop().getaddrinfo(host, 443, type=socket.SOCK_STREAM)
                if not records or any(not ipaddress.ip_address(r[4][0]).is_global for r in records):
                    raise PermissionError('NONPUBLIC_NETWORK_ADDRESS')
                self.resolved.add(host)
            await asyncio.sleep(max(0, 0.2 - (time.monotonic() - self.last)))
            call = {'url': url, 'method': 'GET', 'collected_at': now_ms()}
            self.calls.append(call)
            started = time.monotonic()
            try:
                # Discard vendor cookies; a public probe never carries a session forward.
                self.client.cookies.clear()
                async with self.client.stream('GET', url, headers=headers) as response:
                    call['http_status'] = response.status_code
                    if response.is_redirect:
                        raise PermissionError('REDIRECT_DENIED')
                    response.raise_for_status()
                    raw = bytearray()
                    async for block in response.aiter_bytes():
                        raw.extend(block)
                        if len(raw) > 6 * 1024 * 1024:
                            raise ValueError('RESPONSE_TOO_LARGE')
                self.bytes += len(raw)
                sha = hashlib.sha256(raw).hexdigest()
                compressed = gzip.compress(raw, mtime=0)
                target = self.directory / (sha + '.json.gz')
                # Parent enforces total on disk between isolated providers.
                if not target.exists():
                    target.write_bytes(compressed)
                    target.chmod(0o600)
                assert hashlib.sha256(gzip.decompress(target.read_bytes())).hexdigest() == sha
                call.update(sha256=sha, bytes=len(raw), compressed_bytes=len(compressed), evidence=target.name)
                return json.loads(raw)
            except Exception as exc:
                call['error'] = type(exc).__name__
                raise
            finally:
                call['latency_ms'] = round((time.monotonic() - started) * 1000)
                self.last = time.monotonic()


async def probe(venue, directory):
    started = now_ms()
    e = getattr(ccxt, venue)({'enableRateLimit': True, 'timeout': 5000,
                            'options': {'fetchCurrencies': False, 'adjustForTimeDifference': False,
                                        'maxRetriesOnFailure': 0}})
    e.has['fetchCurrencies'] = False
    transport = SurveyTransport(e, directory)
    e.sign, e.fetch = transport.sign, transport.fetch
    result = {'id': venue, 'started': started, 'ccxt_version': ccxt.__version__, 'status': 'REJECTED',
              'continuous_admission': False, 'live': False, 'fills': 0, 'real_pnl': 0,
              'capabilities': {k: e.has.get(k) for k in ('fetchMarkets', 'fetchOrderBook', 'fetchFundingRate')},
              'samples': [], 'reasons': []}
    try:
        if not e.has.get('fetchMarkets') or not e.has.get('fetchOrderBook'):
            result['reasons'].append('MARKETS_OR_L2_UNSUPPORTED')
            return result
        await asyncio.wait_for(e.load_markets(), 16)
        markets = list(e.markets.values())
        eligible = [m for m in markets if m.get('active') is not False and m.get('base') in {'BTC', 'ETH', 'SOL'}
                    and m.get('quote') in {'USDT', 'USD', 'USDC'} and (m.get('spot') or (m.get('swap') and m.get('linear')))]
        result['catalogue'] = {'total': len(markets), 'liquid_candidates': len(eligible),
                               'spot': sum(bool(m.get('spot')) for m in markets),
                               'linear_perp': sum(bool(m.get('swap') and m.get('linear')) for m in markets)}
        # BTC preferred; two different market types if available; no invented cross-quote matching.
        eligible.sort(key=lambda m: (m.get('base') != 'BTC', m.get('quote') != 'USDT', not m.get('spot'), m['symbol']))
        selected = []
        for kind in ('spot', 'swap'):
            options = [m for m in eligible if m.get(kind)]
            if options:
                selected.append(options[0])
        if not selected:
            result['reasons'].append('NO_SUPPORTED_LIQUID_BTC_ETH_SOL_MARKET')
        for m in selected:
            sample = {'symbol': m['symbol'], 'market': {k: m.get(k) for k in ('id', 'spot', 'swap', 'active', 'base', 'quote', 'settle', 'linear', 'contractSize', 'precision', 'limits', 'taker')},
                      'precision_mode': e.precisionMode, 'reasons': []}
            result['samples'].append(sample)
            try:
                book = await asyncio.wait_for(e.fetch_order_book(m['symbol']), 7)
                sample['book'] = {k: book.get(k) for k in ('timestamp', 'nonce', 'bids', 'asks')}
                sample['received_at'] = now_ms()
                cs = m.get('contractSize') if m.get('swap') else 1
                normalized_book(book, cs)
                sample['reasons'].extend(book_guard(book, sample['received_at']))
                if m.get('taker') is None or not math.isfinite(m['taker']) or m['taker'] < 0:
                    sample['reasons'].append('FEE_UNKNOWN')
                if m.get('active') is not True:
                    sample['reasons'].append('ACTIVITY_UNVERIFIED')
                if m.get('swap') and e.has.get('fetchFundingRate'):
                    f = await asyncio.wait_for(e.fetch_funding_rate(m['symbol']), 7)
                    sample['funding'] = funding_fields(venue, f)
                    ft = sample['funding'].get('source_asof')
                    if not ft or not 0 <= now_ms() - ft <= 300000:
                        sample['reasons'].append('FUNDING_ASOF_UNVERIFIED_OR_STALE')
                elif m.get('swap'):
                    sample['reasons'].append('FUNDING_UNSUPPORTED')
            except Exception as exc:
                sample['reasons'].append(type(exc).__name__)
        if result['samples'] and all(s.get('book') for s in result['samples']):
            result['status'] = 'OBSERVED_RESEARCH_ONLY'
        result['reasons'].extend(r for s in result['samples'] for r in s['reasons'])
        # Two samples cannot establish sustainable reliability. Promotion is a separate explicit gate.
        result['reasons'].append('CONTINUOUS_PROMOTION_REQUIRES_EXACT_ADAPTER_AND_REPEATED_QUALITY_RESOURCE_GATE')
    except asyncio.CancelledError:
        result['reasons'].append('PROVIDER_WALL_TIMEOUT')
    except Exception as exc:
        result['reasons'].append(type(exc).__name__)
    finally:
        await transport.client.aclose()
        await e.close()
        result.update(finished=now_ms(), http=transport.calls, denied=transport.denied,
                      request_count=len(transport.calls), response_bytes=transport.bytes)
        result['reasons'] = sorted(set(result['reasons']))
    return result

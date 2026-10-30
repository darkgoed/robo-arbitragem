"""Exact public GET capability list. No CCXT network fallback, redirects or credentials."""
import asyncio
import hashlib
import json
import time
from urllib.parse import parse_qsl, urlsplit

import httpx

# Only these exact paths may cross the network. Dynamic KuCoin symbols enumerated.
ALLOWED = {
    'okx': {'www.okx.com': {'/api/v5/public/instruments', '/api/v5/market/books', '/api/v5/public/funding-rate'}},
    'kucoin': {
        'api.kucoin.com': {'/api/v3/currencies', '/api/v2/symbols', '/api/v1/market/allTickers', '/api/v1/market/orderbook/level2_20'},
        'api-futures.kucoin.com': {'/api/v1/contracts/active', '/api/v1/level2/depth20'} |
            {f'/api/v1/funding-rate/{symbol}/current' for symbol in ('XBTUSDTM', 'ETHUSDTM', 'SOLUSDTM', 'XRPUSDTM', 'DOGEUSDTM', 'ADAUSDTM', 'LINKUSDTM', 'AVAXUSDTM')},
    },
    'binance': {
        'api.binance.com': {'/api/v3/exchangeInfo', '/api/v3/depth'},
        'fapi.binance.com': {'/fapi/v1/exchangeInfo', '/fapi/v1/depth', '/fapi/v1/premiumIndex'},
    },
    'bingx': {'open-api.bingx.com': {
        '/openApi/spot/v1/common/symbols', '/openApi/spot/v1/market/depth',
        '/openApi/swap/v2/quote/contracts', '/openApi/swap/v2/quote/depth',
        '/openApi/swap/v2/quote/premiumIndex', '/openApi/cswap/v1/market/contracts',
    }},
    'gate': {'api.gateio.ws': {
        '/api/v4/spot/currency_pairs', '/api/v4/margin/currency_pairs',
        '/api/v4/futures/usdt/contracts', '/api/v4/spot/order_book', '/api/v4/futures/usdt/order_book',
    } | {f'/api/v4/futures/usdt/contracts/{b}_USDT' for b in ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'ADA', 'LINK', 'AVAX')}},
}
QUERY_KEYS = {'instType', 'instId', 'sz', 'symbol', 'symbols', 'limit', 'timestamp', 'currency_pair', 'contract', 'with_id'}
HEADER_KEYS = {'accept', 'content-type', 'user-agent', 'accept-encoding', 'x-gate-size-decimal'}


def public_only(venue, url, method='GET', headers=None, body=None):
    try:
        p = urlsplit(url)
        return (method == 'GET' and body is None and p.scheme == 'https'
                and p.username is None and p.password is None and not p.fragment and p.port is None
                and p.path in ALLOWED.get(venue, {}).get(p.hostname, set())
                and all(k in QUERY_KEYS for k, _ in parse_qsl(p.query, keep_blank_values=True))
                and all(k.lower() in HEADER_KEYS for k in (headers or {})))
    except (ValueError, TypeError):
        return False


class PublicTransport:
    """Sequential, bounded and cancellable; logs hashes of original HTTP JSON bytes."""
    def __init__(self, venue):
        self.venue = venue
        self.client = httpx.AsyncClient(timeout=6.5, follow_redirects=False, trust_env=False,
                                        limits=httpx.Limits(max_connections=1, max_keepalive_connections=1))
        self.lock = asyncio.Lock()
        self.calls = []
        self.last = 0
        self.max_calls = 32

    async def fetch(self, url, method='GET', headers=None, body=None):
        if not public_only(self.venue, url, method, headers, body):
            raise PermissionError('NONPUBLIC_ENDPOINT_DENIED')
        async with self.lock:
            if len(self.calls) >= self.max_calls:
                raise RuntimeError('HTTP_BUDGET_EXHAUSTED')
            await asyncio.sleep(max(0, 0.12 - (time.monotonic() - self.last)))
            started = time.monotonic()
            call = {'url': url, 'method': 'GET', 'collected_at': int(time.time() * 1000)}
            self.calls.append(call)
            try:
                async with self.client.stream('GET', url, headers=headers) as response:
                    call['http_status'] = response.status_code
                    if response.is_redirect:
                        raise PermissionError('REDIRECT_DENIED')
                    response.raise_for_status()
                    raw = bytearray()
                    async for chunk in response.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) > 6 * 1024 * 1024:
                            raise ValueError('PUBLIC_RESPONSE_TOO_LARGE')
                call['sha256'] = hashlib.sha256(raw).hexdigest()
                call['bytes'] = len(raw)
                value = json.loads(raw)
                if isinstance(value, dict) and str(value.get('code', '0')) not in {'0', '200000', '200'}:
                    raise ValueError('PUBLIC_API_ERROR')
                return value
            except Exception as exc:
                call['error'] = type(exc).__name__
                call['reason'] = str(exc)[:120] if isinstance(exc, (ValueError, PermissionError)) else type(exc).__name__
                raise
            finally:
                call['latency_ms'] = round((time.monotonic() - started) * 1000)
                self.last = time.monotonic()

    async def close(self):
        await self.client.aclose()

"""Bounded public GET documentation / optional-source audit; never account APIs."""
import asyncio
import hashlib
import json
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
URLS = {
    'okx_docs': 'https://www.okx.com/docs-v5/en/',
    'kucoin_docs': 'https://www.kucoin.com/docs-new/rest/futures-trading/funding-fees/get-current-funding-rate',
    'binance_docs': 'https://developers.binance.com/docs/binance-spot-api-docs/rest-api/general-endpoints',
    'bingx_docs': 'https://bingx-api.github.io/docs-v3/',
    'gate_spot': 'https://api.gateio.ws/api/v4/spot/order_book?currency_pair=BTC_USDT&limit=5',
    'gate_perp': 'https://api.gateio.ws/api/v4/futures/usdt/order_book?contract=BTC_USDT&limit=5',
    'kraken_futures': 'https://futures.kraken.com/derivatives/api/v3/orderbook?symbol=PF_XBTUSD',
    'binance_symbols': 'https://api.binance.com/api/v3/exchangeInfo?symbols=%5B%22BTCUSDT%22,%22ETHUSDT%22,%22SOLUSDT%22,%22XRPUSDT%22,%22DOGEUSDT%22,%22ADAUSDT%22,%22LINKUSDT%22,%22AVAXUSDT%22%5D',
}


async def main():
    report = {'at': time.time(), 'mode': 'PUBLIC_GET_ONLY', 'probes': {}}
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
        for name, url in URLS.items():
            entry = {'url': url}
            start = time.monotonic()
            try:
                async with client.stream('GET', url) as r:
                    entry['http_status'] = r.status_code
                    raw = bytearray()
                    async for chunk in r.aiter_bytes():
                        raw.extend(chunk)
                        if len(raw) >= 2 * 1024 * 1024:
                            entry['truncated'] = True
                            break
                    entry['sha256_observed_bytes'] = hashlib.sha256(raw).hexdigest()
                    if 'docs' in name:
                        text = bytes(raw).decode(errors='replace')
                        # Documentation bytes retained, never executed.
                        (ROOT / 'evidence' / ('expansion-' + name + '.html')).write_text(text)
                        entry['bytes_saved'] = len(raw)
                    else:
                        try:
                            entry['body'] = json.loads(raw)
                        except ValueError:
                            entry['body_excerpt'] = bytes(raw[:400]).decode(errors='replace')
            except Exception as exc:
                entry['error'] = type(exc).__name__
            entry['latency_ms'] = round((time.monotonic() - start) * 1000)
            report['probes'][name] = entry
    (ROOT / 'evidence/expansion-probes.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: {a: b for a, b in v.items() if a != 'body'} | ({'public_error': v['body']} if isinstance(v.get('body'), dict) and 'code' in v['body'] else {}) for k, v in report['probes'].items()}, indent=2))


if __name__ == '__main__':
    asyncio.run(main())

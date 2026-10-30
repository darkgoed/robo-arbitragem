"""Bounded public-only historical backfill. Does not read any application password."""

import asyncio
import json
import resource
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robo.store import Store, now_ms
from robo.history import ingest_funding, ingest_binance_zip, binance_url, coverage, funding_stress

ROOT = Path(__file__).resolve().parents[1]


async def funding_backfill(store):
    from robo.adapters import Adapter

    report = []
    start = now_ms() - 180 * 86400000
    for venue in ["bybit", "bitget"]:
        a = Adapter(venue, store)
        try:
            await a.initialize()
            for base in ["BTC", "ETH", "SOL"]:
                symbol = base + "USDT"
                key = "backfill:" + venue + ":" + symbol
                progress = store.get(key, {})
                if (
                    "--reimport" not in sys.argv
                    and progress.get("state") == "COMPLETED"
                    and progress.get("requested_start", 0) <= start
                ):
                    report.append(
                        progress | {"resumed": "already completed; current head maintained by collector"}
                    )
                    continue
                resume = progress.get("state") == "RUNNING" and "--reimport" not in sys.argv
                cursor = progress.get("cursor", now_ms()) if resume else now_ms()
                first_page = progress.get("next_page", 1) if resume else 1
                requested_start = progress.get("requested_start", start) if resume else start
                previous = None
                pages = 0
                inserted = 0
                oldest = None
                state = {"venue": venue, "symbol": symbol, "requested_start": requested_start, "at": now_ms()}
                for page in range(first_page, min(first_page + 8, 17)):
                    params = {"endTime": cursor} if venue == "bybit" else {"pageNo": page}
                    rates = await a._ex.fetch_funding_rate_history(
                        base + "/USDT:USDT", limit=200 if venue == "bybit" else 100, params=params
                    )
                    if not rates:
                        break
                    earliest = min(r["timestamp"] for r in rates)
                    if previous is not None and earliest >= previous:
                        raise ValueError("pagination did not move backward; refusing duplicate-only loop")
                    selected = [r for r in rates if requested_start <= r["timestamp"] <= now_ms()]
                    result = ingest_funding(store, venue, symbol, selected, a.last_public_url)
                    pages += 1
                    inserted += result["inserted"]
                    oldest = earliest
                    state = {
                        "state": "RUNNING",
                        "venue": venue,
                        "symbol": symbol,
                        "pages": pages,
                        "oldest": oldest,
                        "inserted_this_run": inserted,
                        "requested_start": requested_start,
                        "next_page": page + 1,
                        "cursor": earliest - 1,
                        "at": now_ms(),
                    }
                    store.set(key, state)
                    if earliest <= requested_start or len(rates) < (200 if venue == "bybit" else 100):
                        break
                    previous = earliest
                    cursor = earliest - 1
                    await asyncio.sleep(0.25)
                state.update(
                    state="COMPLETED"
                    if oldest is not None and oldest <= requested_start
                    else "PARTIAL_BOUNDED_OR_SOURCE_LIMIT",
                    pages=pages,
                    inserted_this_run=inserted,
                )
                store.set(key, state)
                report.append(state)
                print(json.dumps(state), flush=True)
        except Exception as e:
            failure = {
                "venue": venue,
                "state": "BLOCKED",
                "error": type(e).__name__ + ": " + str(e)[:240],
                "at": now_ms(),
            }
            store.set("backfill:error:" + venue, failure)
            report.append(failure)
            print(json.dumps(failure), flush=True)
        finally:
            await a.close()
    return report


async def bounded_get(client, url, limit):
    for attempt in range(2):
        try:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                parts = []
                size = 0
                async for part in response.aiter_bytes():
                    size += len(part)
                    if size > limit:
                        raise ValueError("download byte budget exceeded")
                    parts.append(part)
                return b"".join(parts)
        except (httpx.TimeoutException, httpx.ConnectError):
            if attempt:
                raise
            await asyncio.sleep(0.5)
    raise RuntimeError("bounded attempts exhausted")


async def candle_backfill(store):
    now = datetime.now(timezone.utc)
    y = now.year
    m = now.month
    months = []
    for _ in range(2):
        m -= 1
        if m == 0:
            m = 12
            y -= 1
        months.append(f"{y:04d}-{m:02d}")
    report = []
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        for month in sorted(months):
            for market in ["spot", "perp"]:
                for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
                    url = binance_url(market, symbol, month)
                    try:
                        checksum = (
                            (await bounded_get(client, url + ".CHECKSUM", 4096)).decode().split()[0].lower()
                        )
                        raw = await bounded_get(client, url, 4 * 1024 * 1024)
                        result = ingest_binance_zip(store, raw, checksum, market, symbol, month)
                        report.append(result | {"state": "IMPORTED"})
                        print(json.dumps(result), flush=True)
                    except Exception as e:
                        failure = {
                            "source": url,
                            "state": "BLOCKED",
                            "error": type(e).__name__ + ": " + str(e)[:180],
                        }
                        report.append(failure)
                        print(json.dumps(failure), flush=True)
                    await asyncio.sleep(0.25)
    return report


async def main():
    started = time.monotonic()
    store = Store(ROOT / "data")
    report = {
        "at": now_ms(),
        "funding": await funding_backfill(store),
        "candles": await candle_backfill(store),
    }
    report["coverage"] = coverage(store)
    report["research"] = [
        funding_stress(store, v, s) for v in ["bybit", "bitget"] for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    ]
    report["seconds"] = time.monotonic() - started
    report["peak_rss_kib"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    (
        ROOT
        / (
            "evidence/phase2-backfill-reimport.json"
            if "--reimport" in sys.argv
            else "evidence/phase2-backfill.json"
        )
    ).write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "funding_rows": sum(r["count"] for r in report["coverage"]["funding"]),
                "candle_rows": sum(r["count"] for r in report["coverage"]["candles"]),
                "seconds": report["seconds"],
                "peak_rss_kib": report["peak_rss_kib"],
            }
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

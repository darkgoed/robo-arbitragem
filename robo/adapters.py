"""CCXT public-data reuse behind a fail-closed network capability boundary."""

import asyncio
import math
import random
import time
from urllib.parse import urlsplit

import ccxt.async_support as ccxt
from robo.store import now_ms

VENUES = ("bybit", "bitget")
BASES = ("BTC", "ETH", "SOL")
PATHS = {
    "bybit": {
        "/v5/market/instruments-info",
        "/v5/market/orderbook",
        "/v5/market/tickers",
        "/v5/market/funding/history",
    },
    "bitget": {
        "/api/v2/margin/currencies",
        "/api/v2/spot/public/symbols",
        "/api/v2/mix/market/contracts",
        "/api/v2/spot/market/orderbook",
        "/api/v2/mix/market/merge-depth",
        "/api/v2/mix/market/ticker",
        "/api/v2/mix/market/tickers",
        "/api/v2/mix/market/current-fund-rate",
        "/api/v2/mix/market/history-fund-rate",
    },
}


def public_request_allowed(venue, url, method):
    p = urlsplit(url)
    return (
        method == "GET"
        and p.scheme == "https"
        and p.netloc == f"api.{venue}.com"
        and p.path in PATHS.get(venue, set())
        and not p.fragment
    )


def positive(value):
    n = float(value)
    if not math.isfinite(n) or n <= 0:
        raise ValueError("nonpositive or nonfinite market value")
    return n


def rule(market):
    limits, precision = market["limits"], market["precision"]
    return {
        "step": positive(precision["amount"]),
        "tick": positive(precision["price"]),
        "min_qty": positive(limits["amount"].get("min") or precision["amount"]),
        "max_qty": positive(limits["amount"].get("max") or 1e9),
        "min_notional": positive(limits["cost"].get("min") or 5),
        "active": market.get("active") is True,
    }


def book(value):
    if not value.get("timestamp"):
        raise ValueError("missing exchange book timestamp")
    return {
        "bids": [[positive(p), positive(s)] for p, s, *_ in value["bids"][:50]],
        "asks": [[positive(p), positive(s)] for p, s, *_ in value["asks"][:50]],
        "ts": int(value["timestamp"]),
        "sequence": value.get("nonce"),
    }


class Adapter:
    def __init__(self, venue, store):
        if venue not in VENUES:
            raise ValueError("unsupported venue")
        self.venue, self.store = venue, store
        types = ["spot", "linear"] if venue == "bybit" else ["spot", "swap"]
        self._ex = getattr(ccxt, venue)(
            {
                "enableRateLimit": True,
                "timeout": 8000,
                "options": {
                    "fetchMarkets": {"types": types},
                    "fetchCurrencies": False,
                    "adjustForTimeDifference": False,
                },
            }
        )
        self._ex.has["fetchCurrencies"] = False
        self._fetch = self._ex.fetch
        self._ex.fetch = self._guarded_fetch
        self.metadata_at = 0
        self.last_public_url = None
        self._ticker_times = {}
        self.status = {
            "venue": venue,
            "state": "NO_DATA",
            "requests": 0,
            "errors": 0,
            "latency_ms": None,
            "last_success": None,
            "last_error": None,
        }
        self._gate = asyncio.Semaphore(3)

    async def _guarded_fetch(self, url, method="GET", headers=None, body=None):
        if (
            not public_request_allowed(self.venue, url, method)
            or body is not None
            or any(
                "sign" in k.lower()
                or "api-key" in k.lower()
                or "access-key" in k.lower()
                or "authorization" in k.lower()
                for k in (headers or {})
            )
        ):
            raise PermissionError("blocked public transport: " + urlsplit(url).netloc + urlsplit(url).path)
        async with self._gate:
            for attempt in range(2):
                start = time.monotonic()
                self.status["requests"] += 1
                try:
                    result = await self._fetch(url, method, headers, body)
                    elapsed = round((time.monotonic() - start) * 1000)
                    if self.store:
                        self.store.archive(url, result, elapsed)
                    self.status.update(latency_ms=elapsed, last_success=now_ms())
                    self.last_public_url = url
                    if self.venue == "bybit" and urlsplit(url).path == "/v5/market/tickers":
                        for item in result.get("result", {}).get("list", []):
                            self._ticker_times[item["symbol"]] = int(result["time"])
                    return result
                except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RateLimitExceeded) as e:
                    self.status["errors"] += 1
                    self.status["last_error"] = type(e).__name__
                    if attempt:
                        raise
                    await asyncio.sleep(0.5 + random.random() * 0.5)
        raise RuntimeError("unreachable")

    async def initialize(self):
        if self._ex.markets and now_ms() - self.metadata_at < 3600000:
            return
        markets = await self._ex.load_markets(reload=True)
        wanted = {f"{b}/USDT" for b in BASES} | {f"{b}/USDT:USDT" for b in BASES}
        selected = [m for s, m in markets.items() if s in wanted]
        if len(selected) != 6:
            raise ValueError("required spot and linear markets unavailable")
        if any(
            m.get("contract") and (m.get("linear") is not True or m.get("contractSize") != 1)
            for m in selected
        ):
            raise ValueError("unsupported contract units")
        self._ex.set_markets(selected)
        self.metadata_at = now_ms()

    async def snapshot(self, base):
        if base not in BASES:
            raise ValueError("unsupported base")
        await self.initialize()
        spot, perp = base + "/USDT", base + "/USDT:USDT"
        spot_book, perp_book, funding = await asyncio.gather(
            self._ex.fetch_order_book(spot, 50),
            self._ex.fetch_order_book(perp, 50),
            self._ex.fetch_funding_rate(perp),
        )
        sm, pm = self._ex.market(spot), self._ex.market(perp)
        info = funding["info"]
        mark, index = funding.get("markPrice"), funding.get("indexPrice")
        ticker_at = funding.get("timestamp")
        if self.venue == "bitget":
            # CCXT funding structure omits mark/index; public ticker fills precisely this gap.
            ticker = await self._ex.fetch_ticker(perp)
            mark, index = ticker["info"]["markPrice"], ticker["info"]["indexPrice"]
            ticker_at = ticker["timestamp"]
            interval = positive(info["fundingRateInterval"])
        else:
            interval = positive(pm["info"]["fundingInterval"]) / 60
        # CCXT rates may have no timestamp. Public response server timestamp is retained here.
        if not ticker_at:
            ticker_at = self._ticker_times.get(base + "USDT")
        if not ticker_at:
            raise ValueError("funding/ticker exchange timestamp missing")
        rate = float(funding["fundingRate"])
        if not math.isfinite(rate):
            raise ValueError("nonfinite funding")
        return {
            "venue": self.venue,
            "symbol": base + "USDT",
            "source": "ccxt/" + ccxt.__version__,
            "collected_at": now_ms(),
            "metadata_at": self.metadata_at,
            "spot": book(spot_book),
            "perp": book(perp_book),
            "spot_rule": rule(sm),
            "perp_rule": rule(pm),
            "mark": positive(mark),
            "index": positive(index),
            "ticker_at": int(ticker_at),
            "funding_rate": rate,
            "interval_h": interval,
            "next_funding": int(funding.get("fundingTimestamp") or funding.get("nextFundingTimestamp") or 0),
            "spot_fee": max(0.001, positive(sm["taker"])),
            "perp_fee": max(0.0006, positive(pm["taker"])),
            "fee_source": "public metadata / conservative floor; not personalized or historically verified",
        }

    async def history(self, base):
        await self.initialize()
        rates = await self._ex.fetch_funding_rate_history(base + "/USDT:USDT", limit=100)
        if any(not r.get("timestamp") or not math.isfinite(float(r["fundingRate"])) for r in rates):
            raise ValueError("invalid funding history")
        return rates

    async def close(self):
        await self._ex.close()

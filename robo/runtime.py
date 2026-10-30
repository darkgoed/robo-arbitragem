import asyncio
import logging
import time
import httpx
from robo.adapters import Adapter, BASES, VENUES
from robo.engine import scan, quality
from robo.monitor import Monitor
from robo.history import ingest_funding, coverage
from robo.paper import Paper
from robo.store import now_ms

log = logging.getLogger("robo")


class Runtime:
    def __init__(self, store, version="development"):
        self.store = store
        self.version = version
        self.monitor = Monitor(store)
        self.coverage_cache = None
        self.coverage_at = 0
        self.paper = Paper(store)
        self.adapters = [Adapter(v, store) for v in VENUES]
        self.quotes = {}
        for v in VENUES:
            for base in BASES:
                rows = store.snapshots(v, base + "USDT", 1)
                if rows:
                    self.quotes[v + ":" + base + "USDT"] = rows[0]
        self.fx = store.get("fx")
        self.last_cycle = None
        self.budget = {}
        self.history_at = 0
        self.fx_at = 0
        self.maintenance_at = 0

    async def cycle(self):
        for a in self.adapters:
            success = 0
            for base in BASES:
                try:
                    q = await a.snapshot(base)
                    self.store.save_snapshot(q)
                    self.quotes[a.venue + ":" + base + "USDT"] = q
                    success += 1
                    if now_ms() - self.history_at > 300000:
                        rates = await a.history(base)
                        self.store.save_funding(a.venue, base + "USDT", rates)
                        ingest_funding(self.store, a.venue, base + "USDT", rates, a.last_public_url)
                except Exception as e:
                    a.status.update(last_error=type(e).__name__ + ": " + str(e)[:180])
                    self.store.event(
                        "collector_error", {"venue": a.venue, "base": base, "error": type(e).__name__}
                    )
                    log.warning("collector_error venue=%s base=%s type=%s", a.venue, base, type(e).__name__)
            a.status["state"] = "LIVE_DATA" if success == len(BASES) else "DEGRADED"
        if now_ms() - self.history_at > 300000:
            self.history_at = now_ms()
        if now_ms() - self.fx_at > 3600000:
            try:
                async with httpx.AsyncClient(timeout=8, follow_redirects=False) as client:
                    r = await client.get("https://open.er-api.com/v6/latest/USD")
                    r.raise_for_status()
                    value = r.json()
                    if value["result"] != "success" or not 1 < float(value["rates"]["BRL"]) < 100:
                        raise ValueError("invalid FX")
                    self.fx = {
                        "usd_brl": float(value["rates"]["BRL"]),
                        "asof": int(value["time_last_update_unix"]) * 1000,
                        "collected_at": now_ms(),
                        "source": "https://open.er-api.com/v6/latest/USD",
                        "usdt_usd_assumption": 1,
                        "warning": "daily USD/BRL indication; USDT/USD=1 assumed, not executable FX",
                    }
                    self.store.archive(str(r.url), value, 0)
                    self.store.set("fx", self.fx)
            except Exception as e:
                log.warning("fx_error type=%s", type(e).__name__)
            self.fx_at = now_ms()
        self.monitor.evaluate(self.quotes, self.fx, now_ms(), self.version)
        settlement_cache = {}
        for p in self.paper.state()["positions"]:
            end = p["closed"] or now_ms()
            expected = set(range(p.get("next_funding", end + 1), end + 1, p.get("interval_ms", 28800000)))
            if p["status"] == "FAILED_HEDGED" or p.get("short_quantity", p["quantity"]) == 0:
                continue
            if p["closed"] and not p["pending_funding"] and expected.issubset(p["funding_events"]):
                continue
            key = (p["venue"], p["symbol"])
            if key not in settlement_cache:
                settlement_cache[key] = (self.store.history(*key), self.store.snapshots(*key, 5000))
            events, snapshots = settlement_cache[key]
            missing = [
                e["ts"]
                for e in events
                if p["opened"] < e["ts"] <= end
                and e["ts"] not in p["funding_events"]
                and not any(0 <= e["ts"] - s["ticker_at"] <= 60000 for s in snapshots)
            ]
            if missing:
                from robo.archives import archived_query

                for ts in missing:
                    snapshots += archived_query(self.store, *key, ts - 90000, ts + 15000, 10, newest=True)
            self.paper.settle(p["id"], events, snapshots, now_ms())
        self.paper.monitor(self.quotes, now_ms())
        if now_ms() - self.maintenance_at > 3600000:
            self.budget = self.store.maintenance()
            self.maintenance_at = now_ms()
        self.last_cycle = now_ms()
        log.info("cycle_complete quotes=%s venues=%s", len(self.quotes), len(self.adapters))

    async def run(self):
        try:
            while True:
                started = time.monotonic()
                try:
                    await self.cycle()
                except Exception as e:
                    log.exception("cycle_failed type=%s", type(e).__name__)
                await asyncio.sleep(max(1, 30 - (time.monotonic() - started)))
        finally:
            await asyncio.gather(*(a.close() for a in self.adapters))

    def data_coverage(self):
        if self.coverage_cache is None or now_ms() - self.coverage_at > 30000:
            self.coverage_cache = coverage(self.store)
            self.coverage_at = now_ms()
        return self.coverage_cache

    def overview(self, notional=100):
        now = now_ms()
        opps = [scan(q, notional, now) for q in self.quotes.values()]
        paper = self.paper.state()
        mtm, missing = 0.0, []
        for p in paper["positions"]:
            if p["status"] not in {"OPEN", "RESIDUAL_SPOT"}:
                continue
            q = self.quotes.get(p["venue"] + ":" + p["symbol"])
            if not q or quality(q, now):
                missing.append(p["id"])
                continue
            mtm += p["quantity"] * (q["spot"]["bids"][0][0] - p["entry_spot"]) + p.get(
                "short_quantity", p["quantity"]
            ) * (p["entry_perp"] - q["perp"]["asks"][0][0])
        paper["unrealized_mtm"] = None if missing else mtm
        paper["equity_mtm"] = None if missing else paper["balance"] + mtm
        paper["stale_exposure"] = missing
        cross = []
        for base in BASES:
            qs = [self.quotes.get(v + ":" + base + "USDT") for v in VENUES]
            if qs[0] is not None and qs[1] is not None:
                a, b = qs
                cross.append(
                    {
                        "symbol": base + "USDT",
                        "hourly_rate_diff": b["funding_rate"] / b["interval_h"]
                        - a["funding_rate"] / a["interval_h"],
                        "status": "OBSERVATION_ONLY",
                        "reasons": ["cross-venue prefunding, costs and liquidation not qualified"],
                        "stale": bool(quality(a, now) or quality(b, now)),
                    }
                )
        edu = []
        if self.fx and 0 <= now - self.fx["asof"] < 48 * 3600000:
            for brl in [0.3, 0.5]:
                for q in self.quotes.values():
                    candidate = scan(q, brl / self.fx["usd_brl"], now)
                    edu.append(
                        {
                            "brl": brl,
                            "id": candidate["id"],
                            "target_usdt": candidate["target_notional"],
                            "eligible": candidate["eligible"],
                            "reasons": candidate["reasons"],
                        }
                    )
        providers = []
        for a in self.adapters:
            stat = dict(a.status)
            venue_quotes = [q for q in self.quotes.values() if q["venue"] == a.venue]
            stat["fresh_symbols"] = sum(not quality(q, now) for q in venue_quotes)
            stat["state"] = (
                "LIVE_DATA" if stat["fresh_symbols"] == 3 else ("STALE" if venue_quotes else "NO_DATA")
            )
            stat["error_rate"] = stat["errors"] / max(1, stat["requests"])
            providers.append(stat)
        return {
            "now": now,
            "mode": "RESEARCH / PAPER",
            "live_trading": False,
            "last_cycle": self.last_cycle,
            "opportunities": opps,
            "cross_venue": cross,
            "providers": providers,
            "paper": paper,
            "fx": self.fx,
            "educational_sizes": edu,
            "budget": self.budget,
            "policy": {
                "fresh_max_s": 15,
                "poll_s": 30,
                "notice": "Dados ficam STALE após 15s; coleta REST a cada 30s para limitar recursos. Nenhuma ordem real.",
            },
        }

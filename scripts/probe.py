import asyncio
import json
from pathlib import Path
from robo.adapters import Adapter
from robo.store import Store, now_ms
from robo.engine import scan


async def main():
    store = Store(Path(__file__).resolve().parents[1] / "data")
    result = {"collected_at": now_ms(), "venues": {}}
    for venue in ["bybit", "bitget"]:
        a = Adapter(venue, store)
        try:
            q = await a.snapshot("BTC")
            store.save_snapshot(q)
            rates = await a.history("BTC")
            store.save_funding(venue, "BTCUSDT", rates)
            result["venues"][venue] = {
                "quote": q,
                "funding_events": len(rates),
                "scan": scan(q, 100, now_ms()),
            }
        except Exception as e:
            result["venues"][venue] = {"error": str(e), "type": type(e).__name__}
        finally:
            await a.close()
    print(
        json.dumps(
            {
                v: {
                    "funding_events": x.get("funding_events"),
                    "error": x.get("error"),
                    "reasons": x.get("scan", {}).get("reasons"),
                }
                for v, x in result["venues"].items()
            }
        )
    )
    Path("evidence/ccxt-live-probe.json").write_text(json.dumps(result, indent=2))
    assert all("quote" in x for x in result["venues"].values()), "one or more providers failed"


if __name__ == "__main__":
    asyncio.run(main())

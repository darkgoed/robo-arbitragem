"""Read-only public-market discovery. All inputs observed; executions never occur."""

import json
import math
import sqlite3

import ccxt.async_support as ccxt
from robo.engine import common_quantity, vwap
from robo.store import now_ms
from robo.discovery_transport import public_only  # noqa: F401 — public compatibility export

# Live baseline Bybit/Bitget remains untouched. Separate public observer.
VENUES = ("okx", "kucoin", "binance", "bingx", "gate")
BASES = ("BTC", "ETH", "SOL", "XRP", "DOGE", "ADA", "LINK", "AVAX")
BUDGET_BRL = 1000



def nonnegative(v):
    try:
        n = float(v)
    except (TypeError, ValueError, OverflowError):
        return None
    return n if math.isfinite(n) and n >= 0 else None


def pos(v):
    n = nonnegative(v)
    return n if n is not None and n > 0 else None


def public_fx(home):
    try:
        conn = sqlite3.connect(f"file:{home / 'data/research.sqlite3'}?mode=ro", uri=True, timeout=2)
        try:
            row = conn.execute("select body from state where key='fx'").fetchone()
        finally:
            conn.close()
        fx = json.loads(row[0]) if row else None
        if fx and 0 <= now_ms() - int(fx["asof"]) < 48 * 3600000 and 1 < float(fx["usd_brl"]) < 100:
            return {
                "usd_brl": float(fx["usd_brl"]),
                "source": fx.get("source"),
                "asof": int(fx["asof"]),
                "usdt_usd_assumption": 1,
            }
    except (ValueError, TypeError, OSError, sqlite3.Error, KeyError):
        pass
    return None


def normalized_book(raw, contract_size=1):
    if pos(contract_size) is None:
        raise ValueError("INVALID_CONTRACT_SIZE")
    levels = {}
    for side in ("bids", "asks"):
        rows = []
        for line in raw.get(side, [])[:20]:
            price, amount = pos(line[0]), pos(line[1])
            if price is None or amount is None:
                raise ValueError("INVALID_ORDERBOOK")
            rows.append([price, amount * contract_size])
        if not rows or any(pos(a[1]) is None for a in rows):
            raise ValueError("EMPTY_ORDERBOOK")
        if (
            any(rows[i][0] < rows[i + 1][0] for i in range(len(rows) - 1))
            if side == "bids"
            else any(rows[i][0] > rows[i + 1][0] for i in range(len(rows) - 1))
        ):
            raise ValueError("UNSORTED_ORDERBOOK")
        levels[side] = rows
    if levels["bids"][0][0] >= levels["asks"][0][0]:
        raise ValueError("CROSSED_ORDERBOOK")
    return levels


def evaluate(sp, pe, fs, sm, pm, precision_mode, fx, ts, budget_brl=BUDGET_BRL):
    """Conservative ONE upcoming funding-event model, without alleged fills or income."""
    reasons = []
    spot_ts, perp_ts = sp.get("timestamp"), pe.get("timestamp")
    asof_ts = fs.get("source_asof", fs.get("timestamp"))
    info = fs.get("info") or {}
    if not asof_ts:
        for key in ("ts", "updateTime"):
            try:
                candidate = int(info.get(key, 0))
                if candidate > 10**12:
                    asof_ts = candidate
                    break
            except (ValueError, TypeError):
                pass
    funding = nonnegative(fs.get("fundingRate"))
    if funding is None:  # Negative is a valid, observed funding rate, not an invalid value.
        try:
            funding = float(fs["fundingRate"])
        except (TypeError, ValueError, KeyError):
            funding = None
    if funding is None or not math.isfinite(funding) or abs(funding) > 0.01:
        reasons.append("INVALID_FUNDING")
    if not spot_ts or not perp_ts:
        reasons.append("MISSING_EXCHANGE_BOOK_TIMESTAMP")
    else:
        if any(t < ts - 15000 or t > ts + 2000 for t in (spot_ts, perp_ts)):
            reasons.append("STALE_OR_FUTURE_BOOK")
        if abs(spot_ts - perp_ts) > 3000:
            reasons.append("UNSYNCED_BOOKS")
    if not asof_ts:
        reasons.append("FUNDING_SOURCE_TIME_UNVERIFIED")
    elif not (0 <= ts - asof_ts <= 5 * 60 * 1000):
        reasons.append("STALE_FUNDING_SOURCE")
    next_funding = fs.get("fundingTimestamp") or fs.get("nextFundingTimestamp")
    if not next_funding or not ts < next_funding <= ts + 24 * 3600000:
        reasons.append("FUNDING_SCHEDULE_UNVERIFIED")
    if not sm.get("active") or not pm.get("active") or pm.get("linear") is not True:
        reasons.append("MARKET_INACTIVE_OR_NOT_LINEAR")
    contract_size = pos(pm.get("contractSize"))
    if contract_size is None:
        reasons.append("CONTRACT_SIZE_UNKNOWN")
    if precision_mode != ccxt.TICK_SIZE:
        reasons.append("PRECISION_MODE_UNSUPPORTED")
    if fx is None or not pos(fx.get("usd_brl")) or not 0 <= ts - fx.get("asof", 0) < 48 * 3600000:
        reasons.append("FX_UNAVAILABLE")
    spot_taker = nonnegative(sm.get("taker"))
    perp_taker = nonnegative(pm.get("taker"))
    if spot_taker is None or perp_taker is None:
        reasons.append("FEE_METADATA_MISSING")
    try:
        spot = normalized_book(sp)
        perp = normalized_book(pe, contract_size or 1)
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        reasons.append(type(exc).__name__ + "_ORDERBOOK")
        return {
            "status": "REJECT",
            "reasons": sorted(set(reasons)),
            "funding_rate": funding,
            "estimate": None,
            "source_timestamps": {"spot": spot_ts, "perp": perp_ts, "funding": asof_ts},
            "next_funding": next_funding,
            "fills": 0,
        }
    if funding is not None and funding <= 0:
        reasons.append("FUNDING_NONPOSITIVE")
    if reasons:
        return {
            "status": "REJECT",
            "reasons": sorted(set(reasons)),
            "funding_rate": funding,
            "estimate": None,
            "source_timestamps": {"spot": spot_ts, "perp": perp_ts, "funding": asof_ts},
            "next_funding": next_funding,
            "fills": 0,
        }
    try:
        spot_step = pos(sm["precision"]["amount"])
        perp_step = pos(pm["precision"]["amount"])
        if not spot_step or not perp_step:
            raise ValueError("UNKNOWN_AMOUNT_STEP")
        buy_ask = spot["asks"][0][0]
        max_notional = min(400, budget_brl / fx["usd_brl"] / 2.05)
        qty = common_quantity(max_notional / buy_ask, [spot_step, perp_step * contract_size])
        if qty <= 0:
            raise ValueError("LOT_BELOW_MINIMUM")
        for market, leg_qty in ((sm, qty), (pm, qty / contract_size)):
            minimum_qty = nonnegative((market.get("limits") or {}).get("amount", {}).get("min"))
            min_cost = nonnegative((market.get("limits") or {}).get("cost", {}).get("min"))
            if minimum_qty is None or min_cost is None:
                raise ValueError("MINIMUM_NOT_VERIFIED")
            if leg_qty + 1e-12 < minimum_qty:
                raise ValueError("MIN_QUANTITY_NOT_MET")
            if qty * buy_ask + 1e-9 < min_cost:
                raise ValueError("MIN_NOTIONAL_NOT_MET")
            max_qty = pos((market.get("limits") or {}).get("amount", {}).get("max"))
            max_cost = pos((market.get("limits") or {}).get("cost", {}).get("max"))
            if max_qty is not None and leg_qty > max_qty:
                raise ValueError("MAX_AMOUNT_EXCEEDED")
            if max_cost is not None and qty * buy_ask > max_cost:
                raise ValueError("MAX_NOTIONAL_EXCEEDED")
        sp_entry = vwap(spot["asks"], qty)
        sp_exit = vwap(spot["bids"], qty)
        pp_entry = vwap(perp["bids"], qty)
        pp_exit = vwap(perp["asks"], qty)
        # Validate the actual entry side of each leg, not a spot/mid proxy for the short.
        for market, entry_price in ((sm, sp_entry), (pm, pp_entry)):
            cost_limits = (market.get("limits") or {}).get("cost") or {}
            minimum = nonnegative(cost_limits.get("min"))
            maximum = pos(cost_limits.get("max"))
            if minimum is None or qty * entry_price + 1e-9 < minimum:
                raise ValueError("MIN_NOTIONAL_NOT_MET")
            if maximum is not None and qty * entry_price > maximum:
                raise ValueError("MAX_NOTIONAL_EXCEEDED")
        # Conservative floors are explicitly model assumptions, not claimed account fees.
        sf, pf = max(spot_taker, 0.001), max(perp_taker, 0.0006)
        fees = qty * (sp_entry * sf + sp_exit * sf + pp_entry * pf + pp_exit * pf)
        spread = qty * ((sp_entry - sp_exit) + (pp_exit - pp_entry))
        buffer = qty * sp_entry * 0.003
        costs = fees + spread + buffer
        funding_if_unchanged = qty * ((pp_entry + pp_exit) / 2) * funding
        haircut = funding_if_unchanged * 0.5 if funding_if_unchanged > 0 else funding_if_unchanged
        capital = qty * (sp_entry + pp_entry) + costs + qty * sp_entry * 0.01
        if capital > budget_brl / fx["usd_brl"]:
            reasons.append("CAPITAL_NOT_ENOUGH")
        net = haircut - costs
        if net <= 0:
            reasons.append("NET_NONPOSITIVE")
        return {
            "status": "HYPOTHESIS" if not reasons else "REJECT",
            "reasons": reasons,
            "funding_rate": funding,
            "source_timestamps": {"spot": spot_ts, "perp": perp_ts, "funding": asof_ts},
            "next_funding": next_funding,
            "estimate": {
                "base_quantity": qty,
                "perp_contract_quantity": qty / contract_size,
                "contract_size_base": contract_size,
                "capital_required_usdt": capital,
                "capital_required_brl": capital * fx["usd_brl"],
                "funding_one_event_if_rate_unchanged_usdt": funding_if_unchanged,
                "haircut_funding_scenario_usdt": haircut,
                "fees_usdt": fees,
                "spread_depth_usdt": spread,
                "latency_rebalance_buffer_usdt": buffer,
                "net_one_event_usdt": net,
                "spot_taker_fee_metadata": spot_taker,
                "perp_taker_fee_metadata": perp_taker,
                "spot_fee_model": sf,
                "perp_fee_model": pf,
                "model_assumptions": "Public taker fee floors 0.1% spot/0.06% perp, 0.3% stress, funding haircut 50%. NOT real fills, future funding, or guaranteed net.",
            },
            "fills": 0,
        }
    except (ValueError, TypeError, OverflowError, KeyError, ZeroDivisionError) as exc:
        return {
            "status": "REJECT",
            "reasons": [str(exc)[:70]],
            "funding_rate": funding,
            "estimate": None,
            "source_timestamps": {"spot": spot_ts, "perp": perp_ts, "funding": asof_ts},
            "next_funding": next_funding,
            "fills": 0,
        }


from robo.discovery_store import setup, record  # noqa: E402,F401 — compatibility exports


async def run(home):
    from robo.discovery_scanner import Scanner
    return await Scanner(home).cycle()


def summary(home):
    from robo.discovery_scanner import overview
    return overview(home)

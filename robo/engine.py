"""Pure, deterministic research calculations. No execution or network dependencies."""

import math
from decimal import Decimal, ROUND_FLOOR


def funding_pnl(notional: float, rate: float, side: str) -> float:
    if side not in {"long", "short"}:
        raise ValueError("invalid side")
    return notional * rate * (1 if side == "short" else -1)


def forecast_funding(notional, rate, interval_h, next_ms, now_ms, horizon_h):
    if not 0 < interval_h <= 24 or next_ms <= now_ms:
        raise ValueError("invalid funding schedule")
    end = now_ms + horizon_h * 3600000
    count = max(0, math.floor((end - next_ms) / (interval_h * 3600000)) + 1)
    return funding_pnl(notional, rate, "short") * count * (0.5 if rate > 0 else 1)


def common_quantity(target: float, steps: list[float]) -> float:
    ds = [Decimal(str(s)) for s in steps]
    scale = 10 ** max(max(0, -int(str(s.as_tuple().exponent))) for s in ds)
    tick = Decimal(math.lcm(*(int(s * scale) for s in ds))) / scale
    return float((Decimal(str(target)) / tick).to_integral_value(rounding=ROUND_FLOOR) * tick)


def vwap(levels, quantity):
    if quantity <= 0:
        raise ValueError("quantity must be positive")
    remain, value = quantity, 0.0
    for price, size in levels:
        take = min(remain, size)
        value += take * price
        remain -= take
        if remain <= quantity * 1e-10:
            return value / quantity
    raise ValueError("insufficient depth")


def quality(q, now):
    reasons = []
    timestamps = [q["spot"]["ts"], q["perp"]["ts"], q["ticker_at"]]
    if any(now - t > 15000 for t in timestamps):
        reasons.append("STALE_BOOK")
    if any(t - now > 2000 for t in timestamps):
        reasons.append("FUTURE_CLOCK")
    if max(timestamps) - min(timestamps) > 3000:
        reasons.append("UNSYNCED_BOOKS")
    if not 0 <= now - q["metadata_at"] <= 7200000:
        reasons.append("STALE_METADATA")
    for name in ["spot", "perp"]:
        b = q[name]
        if (
            not b["bids"]
            or not b["asks"]
            or any(
                not math.isfinite(v) or v <= 0 for side in ["bids", "asks"] for row in b[side] for v in row
            )
            or b["bids"][0][0] >= b["asks"][0][0]
        ):
            reasons.append("INVALID_BOOK")
        if b["bids"] != sorted(b["bids"], key=lambda r: r[0], reverse=True) or b["asks"] != sorted(
            b["asks"], key=lambda r: r[0]
        ):
            reasons.append("INVALID_BOOK_ORDER")
        if not q[name + "_rule"]["active"]:
            reasons.append("INACTIVE_SYMBOL")
    rate = q["funding_rate"]
    if not math.isfinite(rate) or abs(rate) > 0.01:
        reasons.append("INVALID_FUNDING")
    interval = q["interval_h"]
    if not 0 < interval <= 24 or not now < q["next_funding"] <= now + interval * 3600000 + 60000:
        reasons.append("INVALID_SCHEDULE")
    if not all(math.isfinite(q[x]) and q[x] > 0 for x in ["mark", "index"]):
        reasons.append("INVALID_MARK")
    if not all(math.isfinite(q[x]) and 0 < q[x] < 0.02 for x in ["spot_fee", "perp_fee"]):
        reasons.append("INVALID_FEE")
    if "INVALID_BOOK" not in reasons and "INVALID_MARK" not in reasons:
        for name, reference in [("spot", q["index"]), ("perp", q["mark"])]:
            mid = (q[name]["bids"][0][0] + q[name]["asks"][0][0]) / 2
            if abs(mid / reference - 1) > 0.005:
                reasons.append("PRICE_DRIFT")
    return sorted(set(reasons))


def scan(q, target_notional, now, horizon_h=24, *, quantity=None):
    """Cash long spot / short perp. No basis convergence counted as income."""
    reasons = quality(q, now)
    result = {
        "id": q["venue"] + ":" + q["symbol"],
        "venue": q["venue"],
        "symbol": q["symbol"],
        "strategy": "spot-long/perp-short",
        "target_notional": target_notional,
        "funding_rate": q["funding_rate"] if math.isfinite(q["funding_rate"]) else None,
        "interval_h": q["interval_h"],
        "next_funding": q["next_funding"],
        "age_s": round((now - min(q["spot"]["ts"], q["perp"]["ts"])) / 1000, 2),
        "horizon_h": horizon_h,
        "basis_pct": None,
        "quantity": 0,
        "net_usdt": None,
        "eligible": False,
        "reasons": reasons,
        "gross_funding": None,
        "costs": {},
        "capital_required": None,
        "net_apr_on_capital_pct": None,
        "risk": "HIGH: funding variável; basis, custódia, USDT e liquidação independente",
    }
    if reasons:
        return result
    result["basis_pct"] = 100 * (q["mark"] / q["index"] - 1)
    if abs(result["basis_pct"]) > 5:
        reasons.append("BASIS_OUTLIER")
    if q["funding_rate"] <= 0:
        reasons.append("FUNDING_NONPOSITIVE")
    spot_mid = sum(q["spot"][s][0][0] for s in ["bids", "asks"]) / 2
    steps = [q["spot_rule"]["step"], q["perp_rule"]["step"]]
    qty = common_quantity(target_notional / spot_mid, steps) if quantity is None else quantity
    if quantity is not None and (
        not math.isfinite(qty) or qty <= 0 or any(Decimal(str(qty)) % Decimal(str(s)) != 0 for s in steps)
    ):
        result["reasons"].append("LOT_MISMATCH")
        return result
    result["quantity"] = qty
    if qty <= 0:
        reasons.extend(["BELOW_LOT", "MIN_NOTIONAL"])
        return result
    try:
        buy = vwap(q["spot"]["asks"], qty)
        sell = vwap(q["perp"]["bids"], qty)
        vwap(q["spot"]["bids"], qty)
        vwap(q["perp"]["asks"], qty)
    except ValueError:
        reasons.append("INSUFFICIENT_DEPTH")
        return result
    for rule, price in [(q["spot_rule"], buy), (q["perp_rule"], sell)]:
        if qty < rule["min_qty"] or qty > rule["max_qty"]:
            reasons.append("LOT_LIMIT")
        if qty * price < rule["min_notional"]:
            reasons.append("MIN_NOTIONAL")
    projection = carry_projection(q, qty, now, horizon_h)
    if projection["net_usdt"] <= 0:
        reasons.append("NET_NONPOSITIVE")
    result.update(projection)
    result["eligible"] = not reasons
    result["reasons"] = sorted(set(reasons))
    return result


def carry_projection(q, qty, now, horizon_h=24):
    """Mathematical projection, NOT an execution/fill or a test of lot compatibility."""
    buy, spot_exit = vwap(q["spot"]["asks"], qty), vwap(q["spot"]["bids"], qty)
    sell, perp_exit = vwap(q["perp"]["bids"], qty), vwap(q["perp"]["asks"], qty)
    fee_parts = {
        "spot_entry": qty * buy * q["spot_fee"],
        "spot_exit": qty * spot_exit * q["spot_fee"],
        "perp_entry": qty * sell * q["perp_fee"],
        "perp_exit": qty * perp_exit * q["perp_fee"],
    }
    fees = sum(fee_parts.values())
    impact = qty * ((buy - spot_exit) + (perp_exit - sell))
    drift, rebalance = qty * buy * 0.002, qty * buy * 0.001
    gross = forecast_funding(
        qty * q["mark"], q["funding_rate"], q["interval_h"], q["next_funding"], now, horizon_h
    )
    cost = fees + impact + drift + rebalance
    margin_buffer = qty * buy * 0.01
    capital = qty * (buy + sell) + cost + margin_buffer
    net = gross - cost
    return {
        "entry_spot": buy,
        "entry_perp": sell,
        "quantity": qty,
        "notional": qty * buy,
        "gross_funding": gross,
        "costs": {
            "fees_roundtrip": fees,
            "fee_stages": fee_parts,
            "spread_depth_roundtrip": impact,
            "price_latency_buffer": drift,
            "rebalance_fx_buffer": rebalance,
            "total": cost,
        },
        "prefunding": {
            "spot_paid": qty * buy,
            "short_collateral_100pct": qty * sell,
            "margin_buffer": margin_buffer,
            "roundtrip_cost_reserve": cost,
        },
        "capital_required": capital,
        "net_usdt": net,
        "net_apr_on_capital_pct": net / capital * (365 * 24 / horizon_h) * 100,
    }

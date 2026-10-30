"""Bounded autonomous hypotheses. BRL5 counterfactuals can never create fills."""

import math
import gzip
import hashlib
import json
from robo.store import encode
from decimal import Decimal, ROUND_CEILING
from robo.engine import carry_projection, quality, scan

WINDOW_MS = 300000
EDGE_REASONS = {"NET_NONPOSITIVE", "FUNDING_NONPOSITIVE"}


def fx_rate(fx, now):
    if not fx or not 0 <= now - fx.get("asof", 0) < 48 * 3600000:
        return None
    rate = float(fx.get("usd_brl", 0))
    return rate if math.isfinite(rate) and 1 < rate < 100 else None


def minimum_capital(q, fx, now):
    result = {
        "asof": now,
        "venue": q["venue"],
        "symbol": q["symbol"],
        "quantity": None,
        "capital_usdt": None,
        "capital_brl": None,
        "operationally_compatible": False,
        "reasons": quality(q, now),
        "projection": None,
        "fx": fx,
        "label": "mínimo aproximado segundo metadados/book público; não autorização legal nem convite a depósito",
    }
    if result["reasons"]:
        return result
    s, p = q["spot_rule"], q["perp_rule"]
    ds = [Decimal(str(r["step"])) for r in [s, p]]
    scale = 10 ** max(max(0, -int(str(d.as_tuple().exponent))) for d in ds)
    step = Decimal(math.lcm(*(int(d * scale) for d in ds))) / scale
    required = max(
        s["min_qty"],
        p["min_qty"],
        s["min_notional"] / q["spot"]["asks"][0][0],
        p["min_notional"] / q["perp"]["bids"][0][0],
    )
    qty = (Decimal(str(required)) / step).to_integral_value(rounding=ROUND_CEILING) * step
    # Ceiling is ONLY for the suggested minimum, NEVER for either educational order.
    for _ in range(32):
        amount = float(qty)
        mid = (q["spot"]["bids"][0][0] + q["spot"]["asks"][0][0]) / 2
        candidate = scan(q, amount * mid, now, quantity=amount)
        violations = set(candidate["reasons"]) - EDGE_REASONS
        if violations == {"MIN_NOTIONAL"}:
            qty += step
            continue
        result.update(
            quantity=amount,
            projection=candidate,
            reasons=sorted(violations),
            capital_usdt=candidate.get("capital_required"),
            operationally_compatible=not violations,
        )
        rate = fx_rate(fx, now)
        if rate and result["capital_usdt"] is not None:
            result["capital_brl"] = result["capital_usdt"] * rate
        if not rate:
            result["reasons"].append("FX_UNAVAILABLE")
        return result
    result["reasons"] = ["MINIMUM_NOT_RESOLVED_WITHIN_BOUND"]
    return result


def educational_decision(q, fx, now, trade_brl, minimum=None):
    rate = fx_rate(fx, now)
    result = {
        "scenario": "BRL5",
        "step_brl": trade_brl,
        "asof": now,
        "venue": q["venue"],
        "symbol": q["symbol"],
        "market": q["venue"] + ":" + q["symbol"],
        "decision": "REJECT",
        "category": "DATA_UNAVAILABLE",
        "account_balance_brl": 5,
        "realized_pnl_brl": 0,
        "fills": 0,
        "reasons": [],
        "counterfactual": None,
        "source": q.get("source", "public_snapshot"),
        "quote_ts": min(q["spot"]["ts"], q["perp"]["ts"]),
    }
    invalid = quality(q, now)
    if invalid or not rate:
        result["reasons"] = invalid + ([] if rate else ["FX_UNAVAILABLE"])
        return result
    minimum = minimum or minimum_capital(q, fx, now)
    notional = trade_brl / rate
    candidate = scan(q, notional, now)
    reasons = list(candidate["reasons"])
    if minimum["capital_brl"] is not None and minimum["capital_brl"] > 5:
        reasons.append("INSUFFICIENT_PREFUNDING")
    rounded = candidate["quantity"]
    mid = (q["spot"]["bids"][0][0] + q["spot"]["asks"][0][0]) / 2
    projection = carry_projection(q, notional / mid, now)
    projection.update(label="HIPOTÉTICA INEXEQUÍVEL", fills=0, quantity_ignores_lots=True)
    result.update(
        category="INEXECUTABLE"
        if set(reasons) & {"MIN_NOTIONAL", "LOT_LIMIT", "BELOW_LOT", "INSUFFICIENT_PREFUNDING"}
        else "EDUCATIONAL_NO_EXECUTION",
        reasons=sorted(set(reasons)),
        requested_notional_usdt=notional,
        quantity_rounded_down=rounded,
        minimum_capital_brl=minimum["capital_brl"],
        counterfactual=projection,
    )
    return result


def shadow_capital_decision(minimum, budget_brl=1000):
    # Read-only counterfactual. NEVER opens, settles, or funds paper positions.
    candidate = minimum.get("projection")
    min_brl = minimum.get("capital_brl")
    reasons = list(minimum.get("reasons", []))
    if min_brl is None:
        reasons.append("MINIMUM_CAPITAL_UNAVAILABLE")
    elif min_brl > budget_brl:
        reasons.append("INSUFFICIENT_SHADOW_CAPITAL")
    if candidate:
        reasons.extend(candidate.get("reasons", []))
    reasons = sorted(set(reasons))
    return {
        "scenario": "BRL1000_SHADOW",
        "venue": minimum["venue"],
        "symbol": minimum["symbol"],
        "account_balance_brl": budget_brl,
        "minimum_capital_brl": min_brl,
        "capital_sufficient": min_brl is not None and min_brl <= budget_brl,
        "hypothetical_net_usdt": None if candidate is None else candidate.get("net_usdt"),
        "decision": "PASS" if candidate and not reasons else "REJECT",
        "reasons": reasons,
        "fills": 0,
        "realized_pnl_brl": 0,
        "execution": "NOT_ENABLED",
    }


class Monitor:
    def __init__(self, store):
        self.store = store

    def evaluate(self, quotes, fx, now, version):
        window = now // WINDOW_MS
        with self.store.connect() as db:
            existing = db.execute(
                "SELECT payload,sha256 FROM monitor_runs WHERE window=?", (window,)
            ).fetchone()
        if existing:
            return self._decode(existing)
        decisions = []
        source_quotes = {}
        shadow_brl1000 = []
        available = 0
        for venue in ["bybit", "bitget"]:
            for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
                market = venue + ":" + symbol
                q = quotes.get(market)
                unavailable = "NO_DATA"
                if q:
                    try:
                        encode(q)
                        minimum = minimum_capital(q, fx, now)
                        source_quotes[market] = q
                        available += not quality(q, now)
                        for step in [0.3, 0.5]:
                            decisions.append(educational_decision(q, fx, now, step, minimum))
                        shadow_brl1000.append(shadow_capital_decision(minimum))
                        candidate = minimum["projection"]
                        account = self.store.get("paper", {"balance": 1000, "reserved": 0, "halted": False})
                        available_capital = account["balance"] - account["reserved"]
                        account_reasons = []
                        if (
                            minimum["capital_usdt"] is not None
                            and minimum["capital_usdt"] > available_capital
                        ):
                            account_reasons.append("INSUFFICIENT_RESEARCH_CAPITAL")
                        if candidate and candidate.get("notional", 0) > 400:
                            account_reasons.append("PAPER_NOTIONAL_LIMIT")
                        if account["halted"]:
                            account_reasons.append("PAPER_HALTED")
                        reasons = sorted(
                            set(
                                minimum["reasons"]
                                + ([] if candidate is None else candidate["reasons"])
                                + account_reasons
                            )
                        )
                        decisions.append(
                            {
                                "scenario": "REALISTIC_MINIMUM",
                                "market": market,
                                "venue": venue,
                                "symbol": symbol,
                                "asof": now,
                                "quote_ts": min(q["spot"]["ts"], q["perp"]["ts"]),
                                "source": q.get("source", "public_snapshot"),
                                "decision": "PASS"
                                if minimum["operationally_compatible"] and not reasons
                                else "REJECT",
                                "category": "OPERATIONALLY_COMPATIBLE"
                                if minimum["operationally_compatible"]
                                else "DATA_UNAVAILABLE",
                                "reasons": reasons,
                                "minimum": minimum,
                                "research_available_usdt": available_capital,
                                "projection": candidate,
                                "fills": 0,
                                "hypothetical_roi_24h_pct": None
                                if not candidate or not candidate.get("capital_required")
                                else candidate["net_usdt"] / candidate["capital_required"] * 100,
                            }
                        )
                        continue
                    except (ValueError, KeyError, TypeError, OverflowError):
                        unavailable = "SCHEMA_ERROR"
                for scenario, step in [("BRL5", 0.3), ("BRL5", 0.5), ("REALISTIC_MINIMUM", None)]:
                    decisions.append(
                        {
                            "scenario": scenario,
                            "step_brl": step,
                            "venue": venue,
                            "symbol": symbol,
                            "market": market,
                            "asof": now,
                            "decision": "REJECT",
                            "category": "DATA_UNAVAILABLE",
                            "reasons": [unavailable],
                            "fills": 0,
                        }
                    )
        seen_shadow = {(d["venue"], d["symbol"]) for d in shadow_brl1000}
        for venue in ["bybit", "bitget"]:
            for symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
                if (venue, symbol) not in seen_shadow:
                    shadow_brl1000.append(
                        {
                            "scenario": "BRL1000_SHADOW",
                            "venue": venue,
                            "symbol": symbol,
                            "account_balance_brl": 1000,
                            "minimum_capital_brl": None,
                            "capital_sufficient": False,
                            "hypothetical_net_usdt": None,
                            "decision": "REJECT",
                            "reasons": ["NO_VALID_QUOTE"],
                            "fills": 0,
                            "realized_pnl_brl": 0,
                            "execution": "NOT_ENABLED",
                        }
                    )
        passes = sum(d["decision"] == "PASS" for d in decisions)
        run = {
            "window": window,
            "asof": now,
            "version": version,
            "decisions": decisions,
            "quotes": source_quotes,
            "fx": fx,
            "passes": passes,
            "rejects": len(decisions) - passes,
            "available": available,
            "expected": 6,
            "brl5": {
                "initial": 5,
                "balance": 5,
                "realized_pnl": 0,
                "fills": 0,
                "win_rate": None,
                "baseline": "NO_TRADE",
            },
            "shadow_brl1000": {
                "initial": 1000,
                "balance": 1000,
                "realized_pnl": 0,
                "fills": 0,
                "available_markets": sum(d["capital_sufficient"] for d in shadow_brl1000),
                "positive_hypotheses": sum(d["decision"] == "PASS" for d in shadow_brl1000),
                "decisions": shadow_brl1000,
                "baseline": "NO_TRADE",
            },
            "auto_open": False,
            "live": False,
            "notice": "Hipóteses de snapshot; nunca execução real. AUTO_OPEN=OFF; nenhuma transferência de PnL entre cenários.",
        }
        raw = gzip.compress(encode(run).encode(), mtime=0)
        digest = hashlib.sha256(raw).hexdigest()
        with self.store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            inserted = db.execute(
                "INSERT OR IGNORE INTO monitor_runs VALUES(?,?,?,?,?,?,?,?,?)",
                (window, now, version, raw, digest, passes, run["rejects"], available, 6),
            ).rowcount
            if inserted:
                row = db.execute("SELECT body FROM state WHERE key='hypothesis_totals'").fetchone()
                totals = (
                    json.loads(row[0])
                    if row
                    else {
                        "runs": 0,
                        "decisions": 0,
                        "passes": 0,
                        "rejects": 0,
                        "available": 0,
                        "expected": 0,
                        "first": now,
                    }
                )
                for k, delta in {
                    "runs": 1,
                    "decisions": len(decisions),
                    "passes": passes,
                    "rejects": run["rejects"],
                    "available": available,
                    "expected": 6,
                }.items():
                    totals[k] += delta
                db.execute("INSERT OR REPLACE INTO state VALUES('hypothesis_totals',?)", (encode(totals),))
        return run

    @staticmethod
    def _decode(row):
        if hashlib.sha256(row["payload"]).hexdigest() != row["sha256"]:
            raise ValueError("decision checksum mismatch")
        return json.loads(gzip.decompress(row["payload"]))

    def summary(self, now, limit=12):
        with self.store.connect() as db:
            rows = db.execute(
                "SELECT window,ts,version,passes,rejects,available,expected,sha256 FROM monitor_runs ORDER BY window DESC LIMIT ?",
                (limit,),
            ).fetchall()
            latest = db.execute(
                "SELECT payload,sha256 FROM monitor_runs ORDER BY window DESC LIMIT 1"
            ).fetchone()
            retained = db.execute("SELECT COUNT(*) FROM monitor_runs").fetchone()[0]
        totals = self.store.get("hypothesis_totals", {})
        return {
            "runs": [dict(r) for r in rows],
            "latest": self._decode(latest) if latest else None,
            "latest_current": bool(rows and 0 <= now - rows[0]["ts"] <= 15000),
            "monitor_due_ms": WINDOW_MS,
            "total_runs": totals.get("runs", 0),
            "retained_runs": retained,
            "total_decisions": totals.get("decisions", 0),
            "passes": totals.get("passes", 0),
            "rejections": totals.get("rejects", 0),
            "availability_pct": 100 * totals.get("available", 0) / max(1, totals.get("expected", 0)),
            "hypothesis_pass_rate_pct": 100 * totals.get("passes", 0) / max(1, totals.get("decisions", 0)),
            "brl5": {"balance": 5, "realized_pnl": 0, "fills": 0, "win_rate": None},
            "auto_open": False,
            "live": False,
        }

    def detail(self, window):
        with self.store.connect() as db:
            row = db.execute("SELECT payload,sha256 FROM monitor_runs WHERE window=?", (window,)).fetchone()
        return self._decode(row) if row else None

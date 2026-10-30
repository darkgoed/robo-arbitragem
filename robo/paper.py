"""Virtual ledger only. Logical two-leg execution with explicit stress scenarios."""

import copy
import uuid
from robo.engine import scan, quality, vwap, common_quantity, funding_pnl

INITIAL = {
    "initial": 1000.0,
    "balance": 1000.0,
    "reserved": 0.0,
    "halted": False,
    "positions": [],
    "ledger": [],
    "education": {"initial_brl": 5, "trade_brl": [0.3, 0.5]},
    "mode": "PAPER_ONLY",
    "real_trading": "NOT_IMPLEMENTED",
}


def movement(state, debit, credit, amount, kind, ts, pid):
    state["ledger"].append(
        {
            "ts": ts,
            "position": pid,
            "kind": kind,
            "debit": debit,
            "credit": credit,
            "amount": amount,
            "currency": "VIRTUAL_USDT",
        }
    )
    state["ledger"] = state["ledger"][-2000:]


class Paper:
    def __init__(self, store):
        self.store = store

    def state(self):
        return self.store.get("paper", copy.deepcopy(INITIAL))

    def halt(self, enabled):
        with self.store.mutate("paper", copy.deepcopy(INITIAL)) as state:
            state["halted"] = enabled
        return self.state()

    def open(self, q, notional, now, request_id, research=False, scenario="normal"):
        if scenario not in {"normal", "partial", "second_leg_failure", "compensation_failure"}:
            raise ValueError("invalid simulation scenario")
        with self.store.mutate("paper", copy.deepcopy(INITIAL)) as state:
            duplicate = next((p for p in state["positions"] if p["request_id"] == request_id), None)
            if duplicate:
                original = (
                    duplicate["venue"],
                    duplicate["symbol"],
                    duplicate.get("requested_notional", notional),
                    duplicate["research_override"],
                    duplicate["scenario"],
                )
                if original != (q["venue"], q["symbol"], notional, research, scenario):
                    raise ValueError("IDEMPOTENCY_CONFLICT")
                return duplicate
            if state["halted"]:
                raise ValueError("HALTED")
            if len(state["positions"]) >= 200:
                raise ValueError("LEDGER_POSITION_LIMIT")
            if sum(p["status"] in {"OPEN", "RESIDUAL_SPOT"} for p in state["positions"]) >= 3:
                raise ValueError("MAX_OPEN_POSITIONS")
            result = scan(q, notional, now)
            allowed = {"NET_NONPOSITIVE", "FUNDING_NONPOSITIVE"} if research else set()
            rejected = set(result["reasons"]) - allowed
            if rejected:
                raise ValueError(",".join(sorted(rejected)))
            qty = result["quantity"]
            if scenario == "partial":
                qty = common_quantity(qty * 0.5, [q["spot_rule"]["step"], q["perp_rule"]["step"]])
            if qty <= 0 or any(qty < q[r]["min_qty"] for r in ["spot_rule", "perp_rule"]):
                raise ValueError("PARTIAL_BELOW_MINIMUM")
            buy = vwap(q["spot"]["asks"], qty) * 1.0005  # deterministic 5bp latency stress
            sell = vwap(q["perp"]["bids"], qty) * 0.9995
            if any(qty * p < q[r]["min_notional"] for p, r in [(buy, "spot_rule"), (sell, "perp_rule")]):
                raise ValueError("MIN_NOTIONAL")
            fees = qty * (buy * q["spot_fee"] + sell * q["perp_fee"])
            reserved = qty * (buy + sell)
            if reserved + fees + qty * buy * 0.01 > state["balance"] - state["reserved"]:
                raise ValueError("INSUFFICIENT_VIRTUAL_CAPITAL")
            pid = uuid.uuid4().hex
            position = {
                "id": pid,
                "request_id": request_id,
                "venue": q["venue"],
                "symbol": q["symbol"],
                "status": "OPEN",
                "quantity": qty,
                "requested_quantity": result["quantity"],
                "requested_notional": notional,
                "short_quantity": qty,
                "next_funding": q["next_funding"],
                "interval_ms": int(q["interval_h"] * 3600000),
                "entry_spot": buy,
                "entry_perp": sell,
                "entry_mark": q["mark"],
                "opened": now,
                "closed": None,
                "reserved": reserved,
                "entry_fees": fees,
                "funding_pnl": 0.0,
                "funding_events": [],
                "pending_funding": [],
                "realized_pnl": -fees,
                "research_override": research,
                "scenario": scenario,
                "fill_model": "public depth + adverse latency 5bp; no queue/fill guarantee",
                "stop_loss_usdt": max(1, qty * buy * 0.02),
                "warnings": [],
            }
            if scenario == "second_leg_failure":
                # Model first leg filled, second failed, and emergency flatten at adverse bid.
                exit_spot = vwap(q["spot"]["bids"], qty) * 0.9995
                fees = qty * (buy + exit_spot) * q["spot_fee"]
                pnl = qty * (exit_spot - buy) - fees
                position.update(
                    status="FAILED_HEDGED",
                    closed=now,
                    reserved=0,
                    entry_fees=qty * buy * q["spot_fee"],
                    realized_pnl=pnl,
                    exit_spot=exit_spot,
                    warnings=["SECOND_LEG_FAILED", "SPOT_COMPENSATED_SIMULATED"],
                )
                state["balance"] += pnl
                movement(state, "expense", "cash", -pnl, "failed_leg_compensation", now, pid)
            else:
                if scenario == "compensation_failure":
                    fees, reserved = qty * buy * q["spot_fee"], qty * buy
                    position.update(
                        status="RESIDUAL_SPOT",
                        short_quantity=0,
                        reserved=reserved,
                        entry_fees=fees,
                        realized_pnl=-fees,
                        warnings=["SECOND_LEG_FAILED", "COMPENSATION_FAILED", "DIRECTIONAL_EXPOSURE"],
                    )
                    state["halted"] = True
                state["balance"] -= fees
                state["reserved"] += reserved
                movement(state, "reserved", "cash", reserved, "reserve_1x_two_legs", now, pid)
                movement(state, "expense", "cash", fees, "entry_fees", now, pid)
            state["positions"].append(position)
            return position

    def close(self, pid, q, now, reason="manual"):
        with self.store.mutate("paper", copy.deepcopy(INITIAL)) as state:
            pos = next((p for p in state["positions"] if p["id"] == pid), None)
            if pos is None:
                raise ValueError("UNKNOWN_POSITION")
            if pos["status"] not in {"OPEN", "RESIDUAL_SPOT"}:
                return pos
            if q["venue"] != pos["venue"] or q["symbol"] != pos["symbol"]:
                raise ValueError("WRONG_MARKET")
            if quality(q, now):
                raise ValueError("CLOSE_REQUIRES_FRESH_VALID_DATA")
            qty = pos["quantity"]
            spot_exit = vwap(q["spot"]["bids"], qty) * 0.9995
            perp_exit = vwap(q["perp"]["asks"], qty) * 1.0005
            short_qty = pos.get("short_quantity", qty)
            exit_fees = qty * spot_exit * q["spot_fee"] + short_qty * perp_exit * q["perp_fee"]
            hedge_pnl = qty * (spot_exit - pos["entry_spot"]) + short_qty * (pos["entry_perp"] - perp_exit)
            change = hedge_pnl - exit_fees
            state["balance"] += change
            state["reserved"] = max(0.0, state["reserved"] - pos["reserved"])
            movement(state, "cash", "reserved", pos["reserved"], "release_collateral", now, pid)
            movement(
                state,
                "expense" if change < 0 else "cash",
                "cash" if change < 0 else "income",
                abs(change),
                "exit_pnl_including_fees",
                now,
                pid,
            )
            pos.update(
                status="CLOSED",
                closed=now,
                exit_spot=spot_exit,
                exit_perp=perp_exit,
                exit_fees=exit_fees,
                realized_pnl=pos["realized_pnl"] + change,
                close_reason=reason,
            )
            return pos

    def settle(self, pid, events, snapshots, now):
        with self.store.mutate("paper", copy.deepcopy(INITIAL)) as state:
            pos = next(p for p in state["positions"] if p["id"] == pid)
            if pos["status"] == "FAILED_HEDGED" or pos.get("short_quantity", pos["quantity"]) == 0:
                return
            end = pos["closed"] or now
            expected = set(range(pos.get("next_funding", end + 1), end + 1, pos.get("interval_ms", 28800000)))
            pending = list(expected - {e["ts"] for e in events} - set(pos["funding_events"]))
            for event in events:
                ts = event["ts"]
                if not pos["opened"] < ts <= end or ts in pos["funding_events"]:
                    continue
                marks = [s for s in snapshots if 0 <= ts - s["ticker_at"] <= 60000]
                if not marks:
                    pending.append(ts)
                    continue
                at = max(marks, key=lambda s: s["ticker_at"])
                pnl = funding_pnl(pos["quantity"] * at["mark"], event["rate"], "short")
                state["balance"] += pnl
                pos["funding_pnl"] += pnl
                pos["realized_pnl"] += pnl
                pos["funding_events"].append(ts)
                movement(
                    state,
                    "cash" if pnl >= 0 else "expense",
                    "funding" if pnl >= 0 else "cash",
                    abs(pnl),
                    "real_public_funding_simulated_notional",
                    ts,
                    pid,
                )
            pos["pending_funding"] = sorted(set(pending))

    def monitor(self, quotes, now):
        for p in self.state()["positions"]:
            if p["status"] != "OPEN":
                continue
            q = quotes.get(p["venue"] + ":" + p["symbol"])
            if not q or quality(q, now):
                continue  # no fabricated close prices; UI shows stale exposure
            qty = p["quantity"]
            short_collateral = qty * p["entry_perp"]
            short_equity = short_collateral + qty * (p["entry_perp"] - q["mark"]) + p["funding_pnl"]
            mtm = qty * (
                q["spot"]["bids"][0][0] - p["entry_spot"] + p["entry_perp"] - q["perp"]["asks"][0][0]
            )
            if short_equity < qty * q["mark"] * 0.1 or mtm < -p["stop_loss_usdt"]:
                self.halt(True)
                self.close(p["id"], q, now, "margin_buffer_or_stop")

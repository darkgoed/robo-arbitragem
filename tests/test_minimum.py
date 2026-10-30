"""Synthetic arithmetic fixtures, not historical market evidence."""

import copy
import pytest
from robo.monitor import minimum_capital, educational_decision
from test_scanner import quote


def fx(now=100000000):
    return {
        "usd_brl": 5,
        "asof": now,
        "collected_at": now,
        "source": "synthetic-test",
        "usdt_usd_assumption": 1,
    }


def test_minimum_uses_common_lot_both_legs_four_fees_and_prefunding():
    q = quote()
    q["spot_rule"] = copy.deepcopy(q["spot_rule"])
    q["perp_rule"] = copy.deepcopy(q["perp_rule"])
    q["spot_rule"]["step"] = 0.003
    q["perp_rule"]["step"] = 0.002
    result = minimum_capital(q, fx(), 100000000)
    assert result["quantity"] == 0.054  # smallest common .006 lot above both USD5 minima
    assert result["operationally_compatible"] is True
    assert result["capital_usdt"] > 10.908
    assert result["capital_brl"] == pytest.approx(result["capital_usdt"] * 5)
    assert result["projection"]["costs"]["fees_roundtrip"] > 0
    assert result["projection"]["net_usdt"] < 0
    assert not result["projection"]["eligible"]


def test_five_brl_never_gets_impossible_fractional_fills():
    q = quote()
    for amount in [0.3, 0.5]:
        r = educational_decision(q, fx(), 100000000, amount)
        assert r["decision"] == "REJECT" and r["category"] == "INEXECUTABLE"
        assert r["fills"] == 0 and r["account_balance_brl"] == 5
        assert "INSUFFICIENT_PREFUNDING" in r["reasons"]
        assert "MIN_NOTIONAL" in r["reasons"]
        assert r["counterfactual"]["label"] == "HIPOTÉTICA INEXEQUÍVEL"
        assert r["counterfactual"]["net_usdt"] < 0
        assert r["realized_pnl_brl"] == 0


def test_bad_fx_or_stale_book_do_not_give_current_brl_minimum():
    q = quote()
    assert minimum_capital(q, None, 100000000)["capital_brl"] is None
    assert "STALE_BOOK" in minimum_capital(q, fx(), 100020000)["reasons"]
    assert minimum_capital(q, fx(), 100020000)["capital_usdt"] is None
    old = fx()
    old["asof"] -= 49 * 3600000
    assert minimum_capital(q, old, 100000000)["capital_brl"] is None

"""Fail closed: discovery must never authorize real operations or fictional fills."""

import copy
import pytest
from robo.discovery import public_only, normalized_book, evaluate, setup, summary, record, BUDGET_BRL


def test_read_only_transport_permissions():
    assert public_only("okx", "https://www.okx.com/api/v5/market/books?instId=BTC-USDT")
    assert public_only("kucoin", "https://api-futures.kucoin.com/api/v1/level2/depth20?symbol=XBTUSDTM")
    assert public_only("binance", "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT")
    assert not public_only("okx", "https://www.okx.com/api/v5/trade/order", "POST")
    assert not public_only("okx", "https://www.okx.com/api/v5/account/balance")
    assert not public_only("okx", "https://www.okx.com.evil.example/api/v5/market/books")
    assert not public_only("bingx", "http://open-api.bingx.com/openApi/swap/v2/quote/depth")
    assert not public_only("binance", "https://fapi.binance.com/fapi/v1/order", "POST")
    assert not public_only(
        "okx", "https://www.okx.com/api/v5/market/books", headers={"Authorization": "Bearer fake"}
    )
    assert not public_only("bybit", "https://api.bybit.com/v5/market/orderbook")


def test_normalize_contracts_and_reject_crossed_books():
    book = {"bids": [[100, 3]], "asks": [[101, 2]], "timestamp": 1000000}
    b = normalized_book(book, 0.01)
    assert b["bids"][0][1] == pytest.approx(0.03)
    with pytest.raises(ValueError, match="CROSSED_ORDERBOOK"):
        normalized_book({"bids": [[101, 3]], "asks": [[100, 2]]})


def test_real_data_require_exchange_timestamps():
    ts = 1_000_000_000_000
    book = {"bids": [[100, 10]], "asks": [[100.1, 10]], "timestamp": ts}
    perp = {"bids": [[100.2, 10]], "asks": [[100.3, 10]], "timestamp": ts}
    spm = {
        "active": True,
        "taker": 0.001,
        "precision": {"amount": 0.001},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 5}},
    }
    ppm = {
        "active": True,
        "linear": True,
        "taker": 0.0006,
        "contractSize": 1,
        "precision": {"amount": 0.001},
        "limits": {"amount": {"min": 0.001}, "cost": {"min": 5}},
    }
    fx = {"usd_brl": 5, "asof": ts}
    funding = {"fundingRate": 0.0005, "fundingTimestamp": ts + 3600000, "timestamp": ts}
    import ccxt

    out = evaluate(book, perp, funding, spm, ppm, ccxt.TICK_SIZE, fx, ts)
    assert out["fills"] == 0 and out["estimate"] is not None
    assert out["estimate"]["capital_required_brl"] <= BUDGET_BRL
    bad = copy.deepcopy(book)
    bad["timestamp"] = None
    rejected = evaluate(bad, perp, funding, spm, ppm, ccxt.TICK_SIZE, fx, ts)
    assert rejected["status"] == "REJECT" and "MISSING_EXCHANGE_BOOK_TIMESTAMP" in rejected["reasons"]


def test_discovery_db_is_independent_from_trading_ledger(tmp_path):
    p = setup(tmp_path)
    rid = "run1"
    import sqlite3

    with sqlite3.connect(p) as db:
        db.execute("INSERT INTO discovery_runs VALUES(?,?,?,?,?,?)", (rid, 1234, 1235, 1, 1, 0))
    record(p, rid, "okx", "BTC", {"result": {"status": "REJECT", "reasons": ["NET_NONPOSITIVE"], "fills": 0}})
    out = summary(tmp_path)
    assert out["ready"] and len(out["observations"]) == 1
    assert out["observations"][0]["fills"] == 0
    assert out["live"] is False and out["real_fills"] == 0
    assert not (tmp_path / "data" / "research.sqlite3").exists()

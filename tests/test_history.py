"""Synthetic corrupt/duplicate fixtures; actual ingest evidence is kept separately."""

import io
import zipfile
import hashlib
import pytest
from robo.store import Store
from robo.history import ingest_funding, ingest_binance_zip, normalize_timestamp


def archive():
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(
            "BTCUSDT-5m-2026-09.csv",
            "1788220800000000,100,102,99,101,10,1788221099999999,1000,5,3,300,0\n1788221100000000,101,103,100,102,9,1788221399999999,900,4,2,200,0\n",
        )
    return data.getvalue()


def test_funding_negative_dedup_timestamp_validation_and_conflict(tmp_path):
    s = Store(tmp_path)
    rates = [{"timestamp": 1788220800000, "fundingRate": -0.001}]
    assert (
        ingest_funding(s, "bybit", "BTCUSDT", rates, "https://api.bybit.com/v5/market/funding/history")[
            "inserted"
        ]
        == 1
    )
    assert (
        ingest_funding(s, "bybit", "BTCUSDT", rates, "https://api.bybit.com/v5/market/funding/history")[
            "inserted"
        ]
        == 0
    )
    with pytest.raises(ValueError):
        ingest_funding(
            s, "bybit", "BTCUSDT", [{"fundingRate": 0.001}], "https://api.bybit.com/v5/market/funding/history"
        )
    with pytest.raises(ValueError, match="conflict"):
        ingest_funding(
            s,
            "bybit",
            "BTCUSDT",
            [{"timestamp": 1788220800000, "fundingRate": 0.002}],
            "https://api.bybit.com/v5/market/funding/history",
        )


def test_zip_checksum_units_and_idempotence_do_not_insert_books(tmp_path):
    s = Store(tmp_path)
    raw = archive()
    digest = hashlib.sha256(raw).hexdigest()
    assert normalize_timestamp(1788220800000000) == 1788220800000
    assert normalize_timestamp(1788220800000) == 1788220800000
    r = ingest_binance_zip(s, raw, digest, "spot", "BTCUSDT", "2026-09")
    assert r["rows"] == 2 and r["inserted"] == 2
    assert ingest_binance_zip(s, raw, digest, "spot", "BTCUSDT", "2026-09")["inserted"] == 0
    with s.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 0
        assert db.execute("SELECT MIN(ts) FROM candles").fetchone()[0] == 1788220800000
    with pytest.raises(ValueError, match="checksum"):
        ingest_binance_zip(s, raw, "0" * 64, "spot", "BTCUSDT", "2026-09")
    with pytest.raises(ValueError):
        normalize_timestamp(1788220800)

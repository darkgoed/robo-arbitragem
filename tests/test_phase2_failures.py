import io
import zipfile
import hashlib
import pytest
from robo.store import Store, now_ms
from robo.archives import compact
from robo.history import ingest_binance_zip
from robo.monitor import Monitor, minimum_capital
from test_monitor import quotes
from test_minimum import fx
from test_scanner import quote


def test_atomic_archive_write_failure_keeps_source_l2(tmp_path, monkeypatch):
    s = Store(tmp_path)
    q = quote(now_ms() - 2 * 86400000)
    s.save_snapshot(q)

    def fail(*args):
        raise OSError("synthetic disk interruption")

    monkeypatch.setattr("robo.archives.os.replace", fail)
    with pytest.raises(OSError):
        compact(s, now_ms() - 86400000)
    with s.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM snapshot_archives").fetchone()[0] == 0


def test_missing_funding_interval_does_not_assume_eight_hours(tmp_path):
    qs = quotes(100000000)
    qs["bybit:BTCUSDT"].pop("interval_h")
    r = Monitor(Store(tmp_path)).evaluate(qs, fx(), 100000000, "test")
    rows = [d for d in r["decisions"] if d["market"] == "bybit:BTCUSDT"]
    assert len(rows) == 3 and all("SCHEMA_ERROR" in d["reasons"] for d in rows)
    assert r["available"] == 5


def test_future_fx_is_not_a_valid_currency_comparison():
    value = fx()
    value["asof"] += 1
    assert minimum_capital(quote(), value, 100000000)["capital_brl"] is None


def test_bad_ohlc_page_cannot_partially_commit_or_extract_files(tmp_path):
    store = Store(tmp_path)
    for name, row in [
        ("BTCUSDT-5m-2026-09.csv", "1788220800000000,100,90,99,101,10,1788221099999999,1,1,1,1,0\n"),
        ("../escape.csv", "bad"),
    ]:
        raw = io.BytesIO()
        with zipfile.ZipFile(raw, "w") as z:
            z.writestr(name, row)
        data = raw.getvalue()
        with pytest.raises(ValueError):
            ingest_binance_zip(store, data, hashlib.sha256(data).hexdigest(), "spot", "BTCUSDT", "2026-09")
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM candles").fetchone()[0] == 0
    assert not (tmp_path.parent / "escape.csv").exists()

import pytest
from robo.store import Store
from robo.archives import compact, archived_query, archive_stats
from test_scanner import quote


def test_lossless_archive_preserves_all_l2_and_idempotence(tmp_path, monkeypatch):
    monkeypatch.setattr("robo.archives.now_ms", lambda: 100200000)
    s = Store(tmp_path)
    originals = []
    for i in range(6):
        q = quote(100000000 + i * 30000)
        s.save_snapshot(q)
        originals.append(q)
    result = compact(s, before_ms=100120001, chunk_size=2)
    assert result["archived_rows"] == 5
    assert archived_query(s, "bybit", "BTCUSDT", 0, 100120001, 100) == originals[:5]
    assert s.snapshots("bybit", "BTCUSDT", 100) == originals
    assert compact(s, before_ms=100120001, chunk_size=2)["archived_rows"] == 0
    assert archive_stats(s)["count"] == 5


def test_corrupt_archive_is_not_returned_as_good_data(tmp_path):
    s = Store(tmp_path)
    s.save_snapshot(quote())
    compact(s, before_ms=100000001)
    path = next((tmp_path / "archives").glob("*.gz"))
    path.write_bytes(b"corrupted synthetic file")
    with pytest.raises(ValueError, match="checksum"):
        archived_query(s, "bybit", "BTCUSDT", 0, 200000000, 10)


def test_budget_failure_keeps_original_rows_instead_of_deleting(tmp_path):
    s = Store(tmp_path)
    s.save_snapshot(quote())
    r = compact(s, before_ms=100000001, budget_bytes=1)
    assert r["state"] == "ARCHIVE_BUDGET_BLOCKED"
    with s.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1

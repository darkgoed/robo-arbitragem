"""Synthetic horizon preservation; NEVER reported as real 30-day coverage."""

from robo.store import Store, now_ms
from robo.archives import compact, archived_query
from test_scanner import quote


def test_thirty_five_day_capacity_is_queryable_and_newer_hot_rows_survive(tmp_path):
    store = Store(tmp_path)
    now = now_ms()
    for day in range(35):
        store.save_snapshot(quote(now - day * 86400000))
    result = compact(store, before_ms=now - 86400000)
    assert result["count"] == 33
    rows = archived_query(store, "bybit", "BTCUSDT", now - 35 * 86400000, now, 500)
    assert len(rows) == 33
    assert rows[-1]["collected_at"] - rows[0]["collected_at"] == 32 * 86400000
    assert len(store.snapshots("bybit", "BTCUSDT", 100)) == 35

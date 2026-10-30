import gzip
import hashlib
import json
import pytest
from robo.store import Store, now_ms
from robo.paper import Paper
from test_scanner import quote


def test_hash_raw_funding_dedup_transaction_rollback_and_restart(tmp_path):
    s = Store(tmp_path)
    digest = s.archive("https://api.bybit.com/v5/market/tickers", {"public": True}, 1)
    raw = gzip.decompress((s.raw / (digest + ".json.gz")).read_bytes())
    assert hashlib.sha256(raw).hexdigest() == digest
    assert json.loads(raw)["body"] == {"public": True}
    s.save_funding("bybit", "BTCUSDT", [{"timestamp": 1, "fundingRate": 0.001}] * 2)
    assert len(s.history("bybit", "BTCUSDT")) == 1
    p = Paper(s)
    p.halt(True)
    assert Paper(Store(tmp_path)).state()["halted"] is True
    with pytest.raises(RuntimeError):
        with s.mutate("paper", {}) as state:
            state["halted"] = False
            raise RuntimeError("abort")
    assert p.state()["halted"] is True
    s.save_snapshot(quote(now=now_ms() - 2 * 86400000))
    stats = s.maintenance()
    assert len(s.snapshots("bybit", "BTCUSDT")) == 1
    assert stats["archives"]["count"] == 1
    assert stats["raw_bytes"] == sum(p.stat().st_size for p in s.raw.glob("*.gz"))

"""Offline assertions on captured REAL public quotes, not synthetic profit evidence."""

import json
from pathlib import Path
from robo.engine import quality, scan


def test_both_ccxt_public_captures_keep_units_constraints_and_timestamps():
    evidence = json.loads((Path(__file__).parents[1] / "tests/fixtures/ccxt-live-probe.json").read_text())
    assert set(evidence["venues"]) == {"bybit", "bitget"}
    for venue, item in evidence["venues"].items():
        q = item["quote"]
        assert q["venue"] == venue and q["source"] == "ccxt/4.5.90"
        assert quality(q, q["collected_at"]) == []
        assert q["interval_h"] in {1, 2, 4, 8} and q["next_funding"] > q["collected_at"]
        for kind in ["spot", "perp"]:
            assert q[kind]["asks"][0][0] > q[kind]["bids"][0][0] > 0
            assert q[kind + "_rule"]["min_notional"] > 0
            assert q[kind + "_rule"]["step"] > 0
        assert item["funding_events"] == 100
        # Restoring original collection time is only for this immutable offline fixture.
        # The production path always scans against actual current wall clock.
        assert scan(q, 0.01, q["collected_at"])["eligible"] is False

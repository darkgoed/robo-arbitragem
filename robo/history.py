"""Official retrospective datasets; deliberately separate from executable snapshots."""

import csv
import hashlib
import io
import math
import re
import zipfile
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlsplit

from robo.store import encode, now_ms

BASES = ("BTCUSDT", "ETHUSDT", "SOLUSDT")


def normalize_timestamp(value):
    timestamp = int(value)
    if 10**15 <= timestamp < 10**16:
        timestamp //= 1000  # Binance spot files >=2025 use UTC microseconds.
    if not 1577836800000 <= timestamp <= now_ms() + 2000:
        raise ValueError("timestamp unit/range must be UTC ms or us, not seconds/future")
    return timestamp


def _dataset(db, identifier, kind, source, checksum, first, last, count, size, body):
    db.execute(
        "INSERT OR IGNORE INTO datasets VALUES(?,?,?,?,?,?,?,?,?,?)",
        (identifier, kind, source, checksum, first, last, count, size, now_ms(), encode(body)),
    )


def ingest_funding(store, venue, symbol, rates, source):
    p = urlsplit(source)
    allowed = {"bybit": "/v5/market/funding/history", "bitget": "/api/v2/mix/market/history-fund-rate"}
    if (
        venue not in allowed
        or symbol not in BASES
        or p.scheme != "https"
        or p.netloc != f"api.{venue}.com"
        or p.path != allowed[venue]
    ):
        raise ValueError("unapproved public funding source")
    if len(rates) > 200:
        raise ValueError("funding page too large")
    cleaned = []
    for r in rates:
        if "timestamp" not in r or "fundingRate" not in r:
            raise ValueError("missing funding timestamp/rate schema")
        ts, rate = normalize_timestamp(r["timestamp"]), float(r["fundingRate"])
        if not math.isfinite(rate) or abs(rate) > 1:
            raise ValueError("invalid funding rate")
        cleaned.append((ts, rate))
    raw = encode(cleaned).encode()
    digest = hashlib.sha256(raw).hexdigest()
    identifier = hashlib.sha256((source + digest).encode()).hexdigest()
    inserted = 0
    with store.connect() as db:
        for ts, rate in cleaned:
            previous = db.execute(
                "SELECT rate FROM historical_funding WHERE venue=? AND symbol=? AND ts=?", (venue, symbol, ts)
            ).fetchone()
            if previous and previous[0] != rate:
                raise ValueError("funding revision conflict; page rejected, not silently replaced")
            inserted += db.execute(
                "INSERT OR IGNORE INTO historical_funding VALUES(?,?,?,?,?,?,?)",
                (venue, symbol, ts, rate, now_ms(), source, digest),
            ).rowcount
        if cleaned:
            _dataset(
                db,
                identifier,
                "FUNDING_REALIZED",
                source,
                digest,
                min(t for t, _ in cleaned),
                max(t for t, _ in cleaned),
                len(cleaned),
                len(raw),
                {
                    "venue": venue,
                    "symbol": symbol,
                    "rate_kind": "realized",
                    "timestamp_unit": "UTC ms",
                    "not_execution_backtest": True,
                },
            )
    return {"rows": len(cleaned), "inserted": inserted, "checksum": digest, "source": source}


def binance_url(market, symbol, month):
    if (
        market not in {"spot", "perp"}
        or symbol not in BASES
        or not re.fullmatch(r"20[2-3][0-9]-(0[1-9]|1[0-2])", month)
    ):
        raise ValueError("bounded Binance market/symbol/month required")
    path = "spot" if market == "spot" else "futures/um"
    return f"https://data.binance.vision/data/{path}/monthly/klines/{symbol}/5m/{symbol}-5m-{month}.zip"


def ingest_binance_zip(store, raw, expected_digest, market, symbol, month):
    source = binance_url(market, symbol, month)
    digest = hashlib.sha256(raw).hexdigest()
    if (
        len(raw) > 4 * 1024 * 1024
        or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
        or digest != expected_digest
    ):
        raise ValueError("Binance checksum/size mismatch")
    start = int(datetime.strptime(month, "%Y-%m").replace(tzinfo=timezone.utc).timestamp() * 1000)
    year, mon = map(int, month.split("-"))
    end = int(
        datetime(year + (mon == 12), 1 if mon == 12 else mon + 1, 1, tzinfo=timezone.utc).timestamp() * 1000
    )
    if end > now_ms():
        raise ValueError("monthly archive is not a completed historical month")
    records = []
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        infos = z.infolist()
        filename = f"{symbol}-5m-{month}.csv"
        if (
            len(infos) != 1
            or infos[0].filename != filename
            or infos[0].file_size > 32 * 1024 * 1024
            or infos[0].flag_bits & 1
        ):
            raise ValueError("unexpected archive schema or decompression budget")
        with z.open(infos[0]) as member:
            for number, row in enumerate(csv.reader(io.TextIOWrapper(member, encoding="utf-8"))):
                if number == 0 and row and row[0].lower() in {"open_time", "open time"}:
                    continue
                if len(row) != 12 or len(records) >= 10000:
                    raise ValueError("candle row schema/count invalid")
                ts = normalize_timestamp(row[0])
                close_ts = normalize_timestamp(row[6])
                o, h, low, c, v = map(float, row[1:6])
                if (
                    not start <= ts < end
                    or ts % 300000
                    or not ts <= close_ts <= ts + 299999
                    or any(not math.isfinite(x) or x <= 0 for x in [o, h, low, c])
                    or not math.isfinite(v)
                    or v < 0
                    or h < max(o, low, c)
                    or low > min(o, h, c)
                ):
                    raise ValueError("candle timestamp/OHLCV validation failed")
                records.append((ts, o, h, low, c, v))
    if not records or len({r[0] for r in records}) != len(records):
        raise ValueError("empty/duplicate candle timestamps")
    inserted = 0
    with store.connect() as db:
        for ts, o, h, low, c, v in records:
            previous = db.execute(
                "SELECT open,high,low,close,volume FROM candles WHERE venue='binance' AND market=? AND symbol=? AND timeframe='5m' AND ts=?",
                (market, symbol, ts),
            ).fetchone()
            if previous and tuple(previous) != (o, h, low, c, v):
                raise ValueError("candle revision conflict")
            inserted += db.execute(
                "INSERT OR IGNORE INTO candles VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("binance", market, symbol, "5m", ts, o, h, low, c, v, source, digest, now_ms()),
            ).rowcount
        _dataset(
            db,
            source,
            "BINANCE_CANDLE_BASELINE",
            source,
            digest,
            min(r[0] for r in records),
            max(r[0] for r in records),
            len(records),
            len(raw),
            {
                "market": market,
                "symbol": symbol,
                "timeframe": "5m",
                "timestamp_unit": "UTC ms (spot microseconds normalized)",
                "checksum_url": source + ".CHECKSUM",
                "not_execution_quote": True,
            },
        )
    return {
        "rows": len(records),
        "inserted": inserted,
        "source": source,
        "checksum": digest,
        "bytes": len(raw),
    }


def coverage(store):
    from robo.archives import archive_stats

    with store.connect() as db:
        funding = [
            dict(r)
            for r in db.execute(
                "SELECT venue,symbol,COUNT(*) AS count,MIN(ts) AS first,MAX(ts) AS last,SUM(rate<0) AS negative FROM historical_funding GROUP BY venue,symbol"
            )
        ]
        candles = [
            dict(r)
            for r in db.execute(
                "SELECT venue,market,symbol,timeframe,COUNT(*) AS count,MIN(ts) AS first,MAX(ts) AS last FROM candles GROUP BY venue,market,symbol,timeframe"
            )
        ]
        hot = [
            dict(r)
            for r in db.execute(
                "SELECT venue,symbol,COUNT(*) AS count,MIN(ts) AS first,MAX(ts) AS last FROM snapshots GROUP BY venue,symbol"
            )
        ]
        datasets = [
            dict(r)
            for r in db.execute(
                "SELECT id,kind,source,checksum,first_ts,last_ts,count,bytes,ingested,body FROM datasets ORDER BY ingested DESC LIMIT 40"
            )
        ]
        archived = [
            dict(r)
            for r in db.execute(
                "SELECT venue,symbol,COUNT(*) AS files,SUM(count) AS count,MIN(first_ts) AS first,MAX(last_ts) AS last FROM snapshot_archives GROUP BY venue,symbol"
            )
        ]
        prospective = []
        keys = {(r["venue"], r["symbol"]) for r in hot + archived}
        for venue, symbol in sorted(keys):
            parts = [r for r in hot + archived if r["venue"] == venue and r["symbol"] == symbol]
            first = min(r["first"] for r in parts)
            last = max(r["last"] for r in parts)
            prospective.append(
                {
                    "venue": venue,
                    "symbol": symbol,
                    "count": sum(r["count"] for r in parts),
                    "first": first,
                    "last": last,
                    "span_days": (last - first) / 86400000,
                    "quality": "SPAN_NOT_CONTINUITY; exact L2/gaps queryable in hot + archive",
                }
            )
        for series in funding:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT ts,rate FROM historical_funding WHERE venue=? AND symbol=? ORDER BY ts",
                    (series["venue"], series["symbol"]),
                )
            ]
            deltas = [b["ts"] - a["ts"] for a, b in zip(rows, rows[1:], strict=False)]
            modal = Counter(deltas).most_common(1)[0][0] if deltas else None
            series.update(
                span_days=(series["last"] - series["first"]) / 86400000,
                modal_observed_interval_ms=modal,
                gaps_over_modal=sum(d > modal for d in deltas) if modal else None,
                interval_warning="observed differences only, not proof of the historical funding schedule",
                fixed_100usdt_funding_only=sum(r["rate"] for r in rows) * 100,
                not_pnl_backtest=True,
            )
        for series in candles:
            previous = None
            gaps = 0
            for row in db.execute(
                "SELECT ts FROM candles WHERE venue=? AND market=? AND symbol=? AND timeframe=? ORDER BY ts",
                tuple(series[k] for k in ["venue", "market", "symbol", "timeframe"]),
            ):
                if previous is not None and row[0] - previous > 300000:
                    gaps += 1
                previous = row[0]
            series["gaps_gt5m"] = gaps
    return {
        "at": now_ms(),
        "funding": funding,
        "candles": candles,
        "prospective_hot": hot,
        "prospective_total": prospective,
        "archives": archive_stats(store)
        | {"state": store.get("archive_status", {}).get("state", "NOT_YET_COMPACTED")},
        "datasets": datasets,
        "warning": "Funding retrospectivo e candles Binance são pesquisa separada; NÃO reconstruem L2 nem lucro executável Bybit/Bitget.",
        "live": False,
        "ml": {
            "status": "NOT_TRAINED",
            "promoted": False,
            "reason": "Insufficient prospective/OOS execution evidence and no validated net edge",
        },
    }


def funding_stress(store, venue, symbol, fee_bps=None):
    latest = store.snapshots(venue, symbol, 1)
    if fee_bps is None:
        fee_bps = (
            2 * (latest[0]["spot_fee"] + latest[0]["perp_fee"]) * 10000
            if latest
            else (32 if venue == "bybit" else 52)
        )
    with store.connect() as db:
        rows = [
            dict(r)
            for r in db.execute(
                "SELECT ts,rate FROM historical_funding WHERE venue=? AND symbol=? ORDER BY ts",
                (venue, symbol),
            )
        ]
    days = {}
    for r in rows:
        days.setdefault(r["ts"] // 86400000, []).append(r["rate"])
    gross = [sum(rates) * 100 for _, rates in sorted(days.items())]
    n = len(gross)
    cut = int(n * 0.7)
    segments = {"train_descriptive": gross[:cut], "holdout_descriptive": gross[cut:]}
    return {
        "venue": venue,
        "symbol": symbol,
        "n_days": n,
        "notional_usdt": 100,
        "funding_only": True,
        "fee_assumption": "current public metadata/floors only, NOT historical verified fees",
        "day_grouping": "UTC dates containing at least one event; edge dates can be partial",
        "not_execution_backtest": True,
        "attempt_accounting": "No optimized strategy/model; fixed 70/30 chronological descriptive split, no promotion",
        "segments": {
            k: {
                "n": len(v),
                "gross_fixed_notional_funding": sum(v),
                "daily_mean": sum(v) / len(v) if v else None,
            }
            for k, v in segments.items()
        },
        "cost_sensitivity": [
            {
                "roundtrip_fee_bps": bps,
                "one_day_fee_only_sensitivity_usdt": sum(gross) - n * 100 * bps / 10000,
                "label": "hipótese de roundtrip diário; SEM spreads/books históricos, margem, slippage ou lucro provado",
            }
            for bps in [fee_bps, fee_bps * 1.5, fee_bps * 2]
        ],
        "baseline_no_trade": 0,
        "ml_promoted": False,
    }

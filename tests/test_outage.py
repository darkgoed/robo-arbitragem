import pytest
import ccxt
from robo.adapters import Adapter


async def test_public_transport_retries_once_with_backoff_and_records_errors(monkeypatch):
    adapter = Adapter("bybit", None)
    calls = []
    delays = []

    async def fail_then_succeed(url, method, headers, body):
        calls.append(method)
        if len(calls) == 1:
            raise ccxt.RequestTimeout("synthetic timeout")
        return {"result": {"list": []}, "time": 1}

    async def delay(seconds):
        delays.append(seconds)

    adapter._fetch = fail_then_succeed
    monkeypatch.setattr("robo.adapters.asyncio.sleep", delay)
    try:
        result = await adapter._ex.fetch("https://api.bybit.com/v5/market/tickers", "GET")
        assert result["time"] == 1 and calls == ["GET", "GET"]
        assert len(delays) == 1 and 0.5 <= delays[0] <= 1
        assert adapter.status["errors"] == 1 and adapter.status["requests"] == 2
    finally:
        await adapter.close()


async def test_public_transport_exhausts_bounded_retries(monkeypatch):
    adapter = Adapter("bitget", None)

    async def fail(*args):
        raise ccxt.RequestTimeout("synthetic timeout")

    async def delay(seconds):
        pass

    adapter._fetch = fail
    monkeypatch.setattr("robo.adapters.asyncio.sleep", delay)
    try:
        with pytest.raises(ccxt.RequestTimeout):
            await adapter._ex.fetch("https://api.bitget.com/api/v2/mix/market/ticker", "GET")
        assert adapter.status["requests"] == 2 and adapter.status["errors"] == 2
    finally:
        await adapter.close()

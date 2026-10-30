import pytest
from robo.adapters import public_request_allowed, Adapter


def test_transport_fails_closed_for_financial_or_external_urls():
    assert public_request_allowed("bybit", "https://api.bybit.com/v5/market/orderbook?category=linear", "GET")
    assert not public_request_allowed("bybit", "https://api.bybit.com/v5/order/create", "POST")
    assert not public_request_allowed("bybit", "https://api.bybit.com/v5/order/realtime", "GET")
    assert not public_request_allowed("bitget", "https://evil.test/api/v2/mix/market/contracts", "GET")
    assert not public_request_allowed("bybit", "http://api.bybit.com/v5/market/tickers", "GET")


async def test_ccxt_unsafe_transport_never_reaches_network():
    a = Adapter("bybit", None)
    try:
        with pytest.raises(PermissionError):
            await a._ex.fetch("https://api.bybit.com/v5/order/create", "POST", {}, "{}")
    finally:
        await a.close()

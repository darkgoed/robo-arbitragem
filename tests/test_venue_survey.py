"""SYNTHETIC survey transport adversarial fixtures; never dashboard data."""

import pytest
import httpx
import importlib.util
from pathlib import Path
from robo.venue_survey import public_descriptor, safe_request, api_origins, SurveyTransport
import ccxt.async_support as ccxt


@pytest.mark.parametrize('api,method,path,expected', [
    ('public','GET','markets',True), ('private','GET','markets',False),
    ('public','POST','markets',False), ('public','GET','account/balance',False),
    ('public','GET','orderbook',True), ('public','GET','orders',False),
    ('public','GET','unlisted',False), ('signedPublic','GET','markets',False),
    ('public','GET','withdraw',False),
])
def test_descriptor_must_be_explicit_readonly(api, method, path, expected):
    descriptor = {'public': {'get': {'markets': 1, 'account/balance': 1, 'orderbook': 1, 'orders': 1, 'withdraw': 1}},
                  'private': {'get': {'markets': 1}}, 'signedPublic': {'get': {'markets': 1}}}
    assert public_descriptor(api, method, path, descriptor) == expected


@pytest.mark.parametrize('changed', [
    {'url':'http://api.example.com/markets'}, {'url':'https://127.0.0.1/markets'},
    {'url':'https://user:pass@api.example.com/markets'}, {'url':'https://api.example.com:443/markets'},
    {'url':'https://api.example.com/markets?apiKey=x'}, {'url':'https://api.example.com/markets?signature=x'},
    {'headers':{'Authorization':'x'}}, {'headers':{'Cookie':'x'}},
    {'body':'{}'}, {'method':'POST'}, {'url':'https://evil.example.com/markets'},
])
def test_request_rejects_credentials_unknown_host_post(changed):
    request = {'url':'https://api.example.com/markets','method':'GET','headers':None,'body':None}
    request.update(changed)
    assert not safe_request(request, {'api.example.com'})


def test_nested_api_origins():
    assert api_origins({'public':{'v1':'https://public.example.com/v1'},'private':'https://private.example.com'}) == {'public.example.com','private.example.com'}


async def test_fetch_requires_exact_signed_capability(tmp_path):
    exchange = ccxt.okx()
    transport = SurveyTransport(exchange,tmp_path)
    try:
        with pytest.raises(PermissionError,match='CAPABILITY_DENIED'):
            await transport.fetch('https://www.okx.com/api/v5/public/instruments')
        with pytest.raises(PermissionError,match='NOT_EXPLICIT_PUBLIC_GET'):
            transport.sign('trade/order','private','POST',{})
        assert transport.calls == []
        request = transport.sign('public/instruments','public','GET',{'instType':'SPOT'})
        assert request['url'] in transport.allowed
        assert not transport.calls
    finally:
        await transport.client.aclose()
        await exchange.close()


async def test_survey_redirect_never_followed_and_raw_checksum(tmp_path):
    exchange = ccxt.okx()
    transport = SurveyTransport(exchange, tmp_path)
    await transport.client.aclose()
    seen = []
    def responder(request):
        seen.append(str(request.url))
        return httpx.Response(302, headers={'Location': 'https://evil.example/'})
    transport.client = httpx.AsyncClient(transport=httpx.MockTransport(responder), follow_redirects=False)
    transport.resolved.add('www.okx.com')
    try:
        request = transport.sign('public/instruments', 'public', 'GET', {'instType': 'SPOT'})
        with pytest.raises(PermissionError, match='REDIRECT_DENIED'):
            await transport.fetch(request['url'])
        assert len(seen) == 1 and transport.calls[0]['http_status'] == 302
        assert not list(tmp_path.glob('*.gz'))
    finally:
        await transport.client.aclose()
        await exchange.close()


def test_report_never_accepts_local_timestamp_or_unqualified_promotion():
    path = Path(__file__).resolve().parents[1] / 'scripts/venue-survey-report.py'
    spec = importlib.util.spec_from_file_location('survey_report_test', path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    sample = {'market': {'spot': True, 'swap': False, 'base': 'BTC', 'quote': 'USDT',
                         'precision': {'amount': 0.001}, 'limits': {'amount': {'min': 0.001}, 'cost': {'min': 5}}},
              'precision_mode': 4, 'reasons': [],
              'book': {'bids': [[100, 100]], 'asks': [[101, 100]], 'timestamp': 1790000000000}}
    assert 'BOOK_TIMESTAMP_NOT_PROVEN_IN_RAW' in module.quality_sample(sample, [{'bids': [[100, 100]]}])
    assert module.quality_sample(sample, [{'ts': '1790000000000'}]) == []
    sample['market']['quote'] = 'USDC'
    assert 'QUOTE_OR_SETTLE_NOT_USDT_NOT_ASSUMED_EQUIVALENT' in module.quality_sample(sample, [{'ts': 1790000000000}])
    spot = {'kind': 'spot', 'snapshot_data_gate': 'PASS_AT_CAPTURE', 'base': 'BTC', 'timestamp': 1790000000000}
    assert 'NO_FULLY_QUALIFIED_LINEAR_PERP_SAMPLE' in module.admission([spot])
    perp = spot | {'kind': 'linear_perp', 'timestamp': 1790000004000}
    assert 'NO_MATCHED_SYNCHRONIZED_QUALIFIED_PAIR' in module.admission([spot, perp])
    perp['timestamp'] = spot['timestamp']
    assert 'LONGITUDINAL_RELIABILITY_NOT_PROVEN' in module.admission([spot, perp])

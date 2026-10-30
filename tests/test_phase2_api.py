from test_api import client as client, login
from test_monitor import quotes
from test_minimum import fx
from robo.store import now_ms


def test_monitor_coverage_and_live_gate_are_private_and_scenarios_separate(client):
    for path in ["/api/monitor", "/api/data/coverage", "/api/live-gate", "/api/data/export?kind=candles"]:
        assert client.get(path).status_code == 401
    login(client)
    now = now_ms()
    r = client.app.state.runtime
    r.monitor.evaluate(quotes(now), fx(now), now, "isolated-test")
    data = client.get("/api/monitor").json()
    assert data["brl5"]["balance"] == 5 and data["brl5"]["fills"] == 0 and not data["auto_open"]
    assert len(data["latest"]["decisions"]) == 18
    assert "quotes" not in data["latest"]
    assert len(client.get("/api/monitor/" + str(data["latest"]["window"])).json()["quotes"]) == 6
    assert client.get("/api/data/coverage").json()["candles"] == []
    assert client.get("/api/live-gate").json()["live"] is False
    assert client.get("/api/data/export?kind=decisions").status_code == 200
    assert client.post("/api/monitor", json={"auto_open": True, "live": True}).status_code == 405
    assert client.get("/api/data/snapshots/bybit/BTCUSDT?start=0&end=999999999999999").status_code == 422

import pytest
from fastapi.testclient import TestClient
from robo.app import create_app
from robo.store import now_ms
from test_scanner import quote


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path, collect=False)
    with TestClient(app, base_url="http://localhost:3215") as c:
        c.headers["Origin"] = "http://localhost:3215"
        c.app.state.test_password = (tmp_path / "secrets/initial-password.txt").read_text().strip()
        yield c


def login(c):
    r = c.post("/auth/login", json={"username": "admin", "password": c.app.state.test_password})
    assert r.status_code == 200
    c.headers["X-CSRF-Token"] = r.json()["csrf"]
    return r


def test_auth_all_data_private_no_real_endpoint_or_source_leak(client):
    for path in [
        "/api/overview",
        "/api/events",
        "/api/export/paper",
        "/health/ready",
        "/auth/session",
        "/secrets/auth.json",
        "/.git/config",
        "/data/research.sqlite3",
    ]:
        assert client.get(path).status_code == 401
    assert client.get("/health/live").json()["real_trading"] == "NOT_IMPLEMENTED"
    assert client.get("/").status_code == 200
    r = login(client)
    assert "HttpOnly" in r.headers["set-cookie"] and "SameSite=strict" in r.headers["set-cookie"]
    assert "no-store" in r.headers["cache-control"]
    assert client.get("/health/ready").status_code == 503
    assert client.get("/api/overview").json()["opportunities"] == []
    for path in ["/api/live", "/api/trade", "/secrets/auth.json", "/.git/config"]:
        assert client.get(path).status_code == 404
    assert client.get("/assets/..%2Fsecrets%2Fauth.json").status_code == 404


def test_csrf_host_rate_limit_and_schema(client):
    assert client.post("/auth/login", json={}, headers={"Origin": "https://evil.test"}).status_code == 403
    assert client.get("/", headers={"Host": "evil.test"}).status_code == 400
    login(client)
    assert (
        client.post("/api/paper/halt", json={"halted": True}, headers={"X-CSRF-Token": "bad"}).status_code
        == 403
    )
    assert (
        client.post(
            "/api/paper/open",
            json={"venue": "bybit", "symbol": "XXX", "notional": float(1e10), "request_id": "abcdefgh"},
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/paper/halt", content="x" * 5000, headers={"Content-Type": "application/json"}
        ).status_code
        == 413
    )
    for _ in range(4):
        assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/auth/login", json={"username": "admin", "password": "wrong"}).status_code == 429


def test_paper_api_research_override_is_not_real_and_stale_rejected(client):
    login(client)
    q = quote(now_ms())
    client.app.state.runtime.quotes["bybit:BTCUSDT"] = q
    payload = {
        "venue": "bybit",
        "symbol": "BTCUSDT",
        "notional": 100,
        "request_id": "test-paper-one",
        "research": False,
    }
    assert client.post("/api/paper/open", json=payload).status_code == 409
    payload["research"] = True
    r = client.post("/api/paper/open", json=payload)
    assert r.status_code == 200 and r.json()["status"] == "OPEN"
    pid = r.json()["id"]
    assert client.post("/api/paper/open", json=payload).json()["id"] == pid
    q["spot"]["ts"] -= 20000
    assert client.post("/api/paper/close/" + pid, json={}).status_code == 409
    q["spot"]["ts"] += 20000
    assert client.post("/api/paper/close/" + pid, json={}).json()["status"] == "CLOSED"
    assert client.post("/api/paper/halt", json={"halted": True}).json()["halted"]
    payload["request_id"] = "test-paper-two"
    assert client.post("/api/paper/open", json=payload).status_code == 409
    assert client.get("/api/export/paper").status_code == 200
    assert (
        client.post("/api/experiments", json={"venue": "bybit", "symbol": "BTCUSDT"}).json()["status"]
        == "INSUFFICIENT_DATA"
    )
    assert client.post("/auth/logout", json={}).status_code == 200
    assert client.get("/api/overview").status_code == 401

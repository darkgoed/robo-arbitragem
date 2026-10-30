"""External HTTPS smoke; only virtual application operations, never exchange orders."""

import json
import os
import time
import uuid
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://example.invalid"


def main():
    result = {"at": time.time(), "base": BASE, "checks": []}
    with httpx.Client(base_url=BASE, timeout=15, follow_redirects=False) as c:
        assert c.get("/").status_code == 200
        assert c.get("/api/overview").status_code == 401
        assert c.get("http://example.invalid/").status_code == 301
        r = c.get("/health/live")
        assert r.json()["real_trading"] == "NOT_IMPLEMENTED"
        for path in ["/", "/assets/app.js", "/api/overview", "/bad-path"]:
            response = c.get(path)
            assert response.headers["x-content-type-options"] == "nosniff"
            assert response.headers["x-frame-options"] == "DENY"
        result["checks"].append("TLS verified; HTTP 301; private unauthenticated 401; security headers")
        c.headers["Origin"] = BASE
        login = c.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": Path(
                    os.environ.get("ROBO_TEST_PASSWORD_FILE", ROOT / "secrets/initial-password.txt")
                )
                .read_text()
                .strip(),
            },
        )
        login.raise_for_status()
        cookie = login.headers["set-cookie"]
        assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
        c.headers["X-CSRF-Token"] = login.json()["csrf"]
        initial = c.get("/api/overview").json()
        assert not any(p["status"] in {"OPEN", "RESIDUAL_SPOT"} for p in initial["paper"]["positions"]), (
            "existing exposure: refusing interference"
        )
        if initial["paper"]["halted"]:
            raise RuntimeError("paper already halted; refusing to override operator control")
        deadline = time.monotonic() + 45
        while True:
            overview = c.get("/api/overview").json()
            candidate = next(
                (
                    o
                    for o in overview["opportunities"]
                    if o["age_s"] < 8
                    and o["venue"] == "bitget"
                    and o["symbol"] == "BTCUSDT"
                    and set(o["reasons"]) <= {"NET_NONPOSITIVE", "FUNDING_NONPOSITIVE"}
                ),
                None,
            )
            if candidate:
                break
            assert time.monotonic() < deadline, "no fresh valid real quote"
            time.sleep(1)
        assert len(overview["opportunities"]) == 6
        assert c.get("/health/ready").status_code in {200, 503}
        result["observations"] = overview["opportunities"]
        result["providers"] = overview["providers"]
        result["educational_sizes"] = overview["educational_sizes"]
        payload = {
            "venue": "bitget",
            "symbol": "BTCUSDT",
            "notional": 100,
            "research": True,
            "request_id": "smoke-" + uuid.uuid4().hex,
            "scenario": "normal",
        }
        opened = c.post("/api/paper/open", json=payload)
        opened.raise_for_status()
        pos = opened.json()
        readback = c.get("/api/overview").json()
        assert any(p["id"] == pos["id"] and p["status"] == "OPEN" for p in readback["paper"]["positions"])
        assert c.post("/api/paper/open", json=payload).json()["id"] == pos["id"]
        closed = c.post("/api/paper/close/" + pos["id"], json={})
        closed.raise_for_status()
        out = closed.json()
        assert out["status"] == "CLOSED" and out["entry_fees"] > 0 and out["exit_fees"] > 0
        after = c.get("/api/overview").json()
        target = next(p for p in after["paper"]["positions"] if p["id"] == pos["id"])
        assert (
            target == out
            and abs(after["paper"]["balance"] - initial["paper"]["balance"] - out["realized_pnl"]) < 1e-8
        )
        assert after["paper"]["reserved"] == 0
        result["paper_roundtrip"] = out
        result["checks"].append(
            "real-book virtual two-leg open/readback/idempotency/close/readback, fees and balance reconcile"
        )
        halt = c.post("/api/paper/halt", json={"halted": True})
        halt.raise_for_status()
        assert c.get("/api/overview").json()["paper"]["halted"] is True
        denied = c.post("/api/paper/open", json=payload | {"request_id": "halt-" + uuid.uuid4().hex})
        assert denied.status_code == 409 and "HALTED" in denied.text
        c.post("/api/paper/halt", json={"halted": False}).raise_for_status()
        assert c.get("/api/overview").json()["paper"]["halted"] is False
        result["checks"].append("kill switch blocks new virtual exposure and restoration read back")
        exp = c.post("/api/experiments", json={"venue": "bybit", "symbol": "BTCUSDT", "notional": 100})
        exp.raise_for_status()
        report = exp.json()
        assert any(r["id"] == report["id"] for r in c.get("/api/experiments").json())
        assert report["sharpe"] is None and report["ml"]["promoted"] is False
        result["experiment"] = report
        assert c.get("/api/export/funding").status_code == 200
        assert c.post("/auth/logout", json={}).status_code == 200
        assert c.get("/api/overview").status_code == 401
        result["checks"].append("experiment persisted with no fabricated PnL/Sharpe; CSV; logout revocation")
    result["status"] = "PASS"
    (ROOT / "evidence/http-smoke.json").write_text(json.dumps(result, indent=2))
    print(
        json.dumps(
            {
                "status": result["status"],
                "checks": result["checks"],
                "paper_pnl_virtual_usdt": out["realized_pnl"],
                "dataset_status": report["status"],
            }
        )
    )


if __name__ == "__main__":
    main()

"""Read back the deployed release and actual fresh/stale transitions, no virtual trades."""

import json
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://example.invalid"


def main():
    report = {"at": time.time(), "url": BASE, "checks": []}
    with httpx.Client(base_url=BASE, timeout=15) as c:
        assert c.get("/").status_code == 200
        assert c.get("/api/overview").status_code == 401
        c.headers["Origin"] = BASE
        r = c.post(
            "/auth/login",
            json={
                "username": "admin",
                "password": (ROOT / "secrets/initial-password.txt").read_text().strip(),
            },
        )
        r.raise_for_status()
        c.headers["X-CSRF-Token"] = r.json()["csrf"]
        deadline = time.monotonic() + 45
        while True:
            overview = c.get("/api/overview").json()
            if (
                sum(v["fresh_symbols"] for v in overview["providers"]) == 6
                and max(o["age_s"] for o in overview["opportunities"]) < 12
            ):
                break
            assert time.monotonic() < deadline, "no synchronized fresh cycle"
            time.sleep(1)
        assert c.get("/health/ready").status_code == 200
        assert len(overview["opportunities"]) == 6 and overview["live_trading"] is False
        assert not any(p["status"] in {"OPEN", "RESIDUAL_SPOT"} for p in overview["paper"]["positions"])
        report["fresh"] = {
            "providers": overview["providers"],
            "observations": overview["opportunities"],
            "educational_sizes": overview["educational_sizes"],
        }
        report["paper"] = {
            "balance_virtual": overview["paper"]["balance"],
            "positions": len(overview["paper"]["positions"]),
            "reserved": overview["paper"]["reserved"],
            "halted": overview["paper"]["halted"],
        }
        denied = c.post("/api/paper/halt", json={"halted": False, "mode": "real"})
        assert denied.status_code == 422
        report["checks"].append(
            "6 real markets fresh; readiness200; real-mode field rejected; no residual exposure"
        )
        r = c.post("/api/experiments", json={"venue": "bitget", "symbol": "BTCUSDT", "notional": 100})
        r.raise_for_status()
        exp = r.json()
        assert any(e["id"] == exp["id"] for e in c.get("/api/experiments").json())
        assert exp["version"].startswith("0.1.0@") and "uncommitted" not in exp["version"]
        assert exp["status"] == "INSUFFICIENT_DATA" and exp["paper_net_pnl"] is None
        report["experiment"] = exp
        report["checks"].append(
            "experiment persisted with deployed commit and honest insufficient-data result"
        )
        deadline = time.monotonic() + 45
        while True:
            stale = c.get("/api/overview").json()
            if sum(v["fresh_symbols"] for v in stale["providers"]) == 0:
                break
            assert time.monotonic() < deadline, "no natural stale window observed"
            time.sleep(1)
        assert c.get("/health/ready").status_code == 503
        assert all(not o["eligible"] and "STALE_BOOK" in o["reasons"] for o in stale["opportunities"])
        report["stale"] = {"providers": stale["providers"], "observations": stale["opportunities"]}
        report["checks"].append("natural stale cycle explicitly STALE; readiness503; all entries blocked")
        c.post("/auth/logout", json={}).raise_for_status()
        assert c.get("/api/overview").status_code == 401
    report["status"] = "PASS"
    (ROOT / "evidence/final-readback.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "status": report["status"],
                "version": exp["version"],
                "checks": report["checks"],
                "paper": report["paper"],
            }
        )
    )


if __name__ == "__main__":
    main()

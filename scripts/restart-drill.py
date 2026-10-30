"""Restart only this service; assert virtual ledger persistence and session revocation."""

import hashlib
import json
import subprocess
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://example.invalid"


def login(client):
    client.headers["Origin"] = BASE
    response = client.post(
        "/auth/login",
        json={"username": "admin", "password": (ROOT / "secrets/initial-password.txt").read_text().strip()},
    )
    response.raise_for_status()
    client.headers["X-CSRF-Token"] = response.json()["csrf"]


def main():
    with httpx.Client(base_url=BASE, timeout=10) as c:
        login(c)
        before = c.get("/api/overview").json()["paper"]
        assert not any(p["status"] in {"OPEN", "RESIDUAL_SPOT"} for p in before["positions"])
        c.post("/api/paper/halt", json={"halted": True}).raise_for_status()
        assert c.get("/api/overview").json()["paper"]["halted"] is True
        subprocess.run(
            ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "restart", "robo-arbitrage.service"], check=True
        )  # noqa: S603
        deadline = time.monotonic() + 20
        while True:
            try:
                if c.get("/health/live").status_code == 200:
                    break
            except httpx.TransportError:
                pass
            assert time.monotonic() < deadline, "restart readiness timeout"
            time.sleep(0.5)
        assert c.get("/api/overview").status_code == 401
        login(c)
        after = c.get("/api/overview").json()["paper"]
        for key in ["positions", "ledger", "balance", "reserved", "initial"]:
            assert before[key] == after[key], key
        assert after["halted"] is True
        c.post("/api/paper/halt", json={"halted": before["halted"]}).raise_for_status()
        assert c.get("/api/overview").json()["paper"]["halted"] == before["halted"]
        result = {
            "status": "PASS",
            "at": time.time(),
            "checks": [
                "halt persisted",
                "ledger/balance/positions survived own service restart",
                "old session revoked",
                "operator halt state restored and read back",
            ],
            "position_count": len(after["positions"]),
            "balance_virtual": after["balance"],
            "ledger_sha256": hashlib.sha256(json.dumps(after["ledger"], sort_keys=True).encode()).hexdigest(),
        }
        (ROOT / "evidence/restart-drill.json").write_text(json.dumps(result, indent=2))
        c.post("/auth/logout", json={}).raise_for_status()
        print(json.dumps(result))


if __name__ == "__main__":
    main()

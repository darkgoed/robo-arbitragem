"""Own-service rollout + real v0.1 rollback drill, no password plaintext or other apps."""

import gzip
import hashlib
import json
import sqlite3
import subprocess
import time
from pathlib import Path
import httpx

ROOT = Path(__file__).resolve().parents[1]
BASE = "https://example.invalid"


def command(args):
    p = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, timeout=40, check=False)  # noqa: S603
    if p.returncode:
        raise RuntimeError("command failed: " + " ".join(args) + " " + p.stderr[:200])
    return p.stdout.strip()


def fingerprint():
    return hashlib.sha256((ROOT / "secrets/auth.json").read_bytes()).hexdigest()


def data_state():
    with sqlite3.connect(ROOT / "data/research.sqlite3") as db:
        paper = db.execute("SELECT body FROM state WHERE key='paper'").fetchone()
        return {
            "paper": json.loads(paper[0]) if paper else None,
            "funding": db.execute("SELECT COUNT(*) FROM historical_funding").fetchone()[0],
            "candles": db.execute("SELECT COUNT(*) FROM candles").fetchone()[0],
            "quick_check": db.execute("PRAGMA quick_check").fetchone()[0],
        }


def restart(version):
    command(["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "restart", "robo-arbitrage.service"])
    deadline = time.monotonic() + 25
    while True:
        try:
            r = httpx.get(BASE + "/health/live", timeout=5)
            if r.status_code == 200 and (version is None or r.json().get("version", "").startswith(version)):
                assert r.json()["real_trading"] == "NOT_IMPLEMENTED"
                return r.json()
        except httpx.TransportError:
            pass
        assert time.monotonic() < deadline, "service health timeout"
        time.sleep(0.3)


def monitor_readback(candidate):
    deadline = time.monotonic() + 35
    while True:
        with sqlite3.connect(ROOT / "data/research.sqlite3") as db:
            row = db.execute(
                "SELECT payload,sha256 FROM monitor_runs ORDER BY window DESC LIMIT 1"
            ).fetchone()
        if row:
            assert hashlib.sha256(row[0]).hexdigest() == row[1]
            body = json.loads(gzip.decompress(row[0]))
            if body["version"] == "0.2.0@" + candidate:
                assert (
                    len(body["decisions"]) == 18
                    and body["brl5"]["balance"] == 5
                    and body["brl5"]["fills"] == 0
                )
                assert not body["auto_open"] and not body["live"]
                return body
        assert time.monotonic() < deadline, "autonomous decision persisted readback timeout"
        time.sleep(0.5)


def main():
    assert json.loads((ROOT / "evidence/phase2-gates.json").read_text())["status"] == "PASS"
    assert json.loads((ROOT / "evidence/phase2-browser.json").read_text())["status"] == "PASS"
    command(["git", "diff", "--exit-code", "--", "robo", "web", "VERSION"])
    candidate = command(["git", "rev-parse", "HEAD"])
    before = data_state()
    auth_before = fingerprint()
    report = {
        "at": time.time(),
        "candidate": candidate,
        "baseline": command(["git", "rev-parse", "v0.1.0"]),
        "before": before,
        "checks": [],
    }
    try:
        report["new_first_health"] = restart("0.2.0@" + candidate)
        report["first_decision"] = monitor_readback(candidate)
        assert httpx.get(BASE + "/api/monitor", timeout=10).status_code == 401
        report["checks"].append(
            "v02 process version, anonymous API401 and actual autonomous SQLite decision readback"
        )
        command(["git", "restore", "--source", "v0.1.0", "--", "robo", "web", "VERSION"])
        report["rollback_health"] = restart(None)
        old = httpx.get(BASE + "/", timeout=10)
        assert old.status_code == 200 and 'id="brl-experiment"' not in old.text
        assert data_state()["paper"] == before["paper"] and fingerprint() == auth_before
        report["checks"].append(
            "actual v010 code+UI restored, own service healthy, ledger and rotated auth unchanged"
        )
    finally:
        command(["git", "restore", "--source", candidate, "--", "robo", "web", "VERSION"])
        report["final_health"] = restart("0.2.0@" + candidate)
    report["decision_readback"] = monitor_readback(candidate)
    after = data_state()
    assert (
        after["paper"] == before["paper"]
        and after["candles"] == before["candles"]
        and after["funding"] >= before["funding"]
        and after["quick_check"] == "ok"
    )
    assert fingerprint() == auth_before
    for path in [
        "/api/monitor",
        "/api/data/coverage",
        "/api/live-gate",
        "/api/data/export?kind=funding",
        "/secrets/auth.json",
    ]:
        r = httpx.get(BASE + path, timeout=10)
        assert r.status_code == 401
        assert r.headers["x-content-type-options"] == "nosniff"
    assert httpx.get(BASE + "/assets/phase2.js", timeout=10).status_code == 200
    report.update(
        after=after,
        auth_fingerprint_unchanged=True,
        positive_auth_production="NOT_EXERCISED_USER_SECRET_PRESERVED; isolated staging validated",
        status="PASS",
    )
    report["checks"].append(
        "v02 restored/read back; all private paths401; public phase2 asset200; source datasets and ledger preserved"
    )
    (ROOT / "evidence/phase2-deploy-rollback.json").write_text(json.dumps(report, indent=2))
    print(
        json.dumps(
            {
                "status": report["status"],
                "candidate": candidate,
                "checks": report["checks"],
                "final_health": report["final_health"],
                "decision_rejects": report["decision_readback"]["rejects"],
                "auth_fingerprint_unchanged": True,
            }
        )
    )


if __name__ == "__main__":
    main()

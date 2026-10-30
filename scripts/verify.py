"""Deterministic project-scoped release gates; saves real command outputs."""

import json
import sys
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
commands = [
    [".venv/bin/ruff", "format", "--check", "robo", "tests", "scripts"],
    [".venv/bin/ruff", "check", "robo", "tests", "scripts"],
    [".venv/bin/python", "-m", "pytest", "-q", "tests"],
    [".venv/bin/python", "-m", "compileall", "-q", "robo", "scripts"],
    ["node", "--check", "web/app.js"],
    ["node", "--check", "web/phase2.js"],
    ["node", "--check", "scripts/browser-phase2.cjs"],
    ["node", "--check", "scripts/browser-smoke.cjs"],
    [".venv/bin/python", "scripts/policy-check.py"],
    ["uv", "pip", "check", "--python", ".venv/bin/python"],
    ["git", "diff", "--check"],
]
results = []
for command in commands:
    start = time.monotonic()
    process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=90, check=False)  # noqa: S603
    result = {
        "command": command,
        "exit_code": process.returncode,
        "seconds": round(time.monotonic() - start, 3),
        "output": process.stdout + process.stderr,
    }
    results.append(result)
    print("PASS" if process.returncode == 0 else "FAIL", " ".join(command))
report = {
    "at": time.time(),
    "status": "PASS" if all(r["exit_code"] == 0 for r in results) else "FAIL",
    "results": results,
}
(ROOT / ("evidence/phase2-gates.json" if "--phase2" in sys.argv else "evidence/gates.json")).write_text(
    json.dumps(report, indent=2)
)
assert report["status"] == "PASS", "One or more real release gates failed; see evidence/gates.json"

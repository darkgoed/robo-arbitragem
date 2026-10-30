"""Temporary isolated localhost smoke target; same runtime code, test-only auth/data."""

import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
os.environ["ROBO_HOME"] = str(ROOT / "data/staging-v02")
sys.path.insert(0, str(ROOT))
from robo.app import create_app  # noqa: E402 — isolated home must be set before importing app
import uvicorn  # noqa: E402 — isolated process startup

if __name__ == "__main__":
    app = create_app(ROOT / "data/staging-v02", collect=False, test_origin="http://localhost:3216")
    uvicorn.run(app, host="127.0.0.1", port=3216, access_log=False)

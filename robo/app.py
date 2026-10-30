import asyncio
import contextlib
import csv
import hmac
import io
import json
import logging
import os
import sqlite3
import subprocess
from contextlib import asynccontextmanager
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request, Query
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field, ConfigDict
from starlette.middleware.trustedhost import TrustedHostMiddleware
from robo.backtest import run_research
from robo.runtime import Runtime
from robo.live_gate import live_gate
from robo.history import funding_stress
from robo.archives import archived_query
from robo.security import Auth
from robo.store import Store, now_ms
from robo.discovery_scanner import Scanner, overview as discovery_overview
from robo.discovery_store import history as discovery_history, detail as discovery_detail

HOME = Path(os.environ.get("ROBO_HOME", Path(__file__).resolve().parents[1]))
ORIGINS = {"http://127.0.0.1:3215", "http://localhost:3215", os.environ.get("ROBO_PUBLIC_ORIGIN", "https://example.invalid")}


class Login(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=1, max_length=50)
    password: str = Field(min_length=1, max_length=200)


class Market(BaseModel):
    model_config = ConfigDict(extra="forbid")
    venue: Literal["bybit", "bitget"]
    symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    notional: float = Field(default=100, gt=0, le=400, allow_inf_nan=False)


class Open(Market):
    request_id: str = Field(min_length=8, max_length=80, pattern=r"^[a-zA-Z0-9_-]+$")
    research: bool = False
    scenario: Literal["normal", "partial", "second_leg_failure", "compensation_failure"] = "normal"


class Halt(BaseModel):
    model_config = ConfigDict(extra="forbid")
    halted: bool


class DiscoveryScenarios(BaseModel):
    model_config = ConfigDict(extra="forbid")
    budgets_brl: list[float] = Field(min_length=1, max_length=5)


def create_app(home=HOME, collect=True, test_origin=None):
    home = Path(home)
    code_commit = "uncommitted"
    if (home / ".git").exists():
        proc = subprocess.run(  # noqa: S603 — fixed executable/args, local repository path only
            ["/usr/bin/git", "-C", str(home), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )  # noqa: S603
        code_commit = proc.stdout.strip() or "uncommitted"
    store = Store(home / "data")
    auth = Auth(home / "secrets")
    release_path = home / "VERSION"
    if not release_path.exists():
        release_path = Path(__file__).resolve().parents[1] / "VERSION"
    version = release_path.read_text().strip() + "@" + code_commit
    runtime = Runtime(store, version)
    discovery = Scanner(home)
    allowed_origins = set(ORIGINS)
    if test_origin is not None:
        if test_origin != "http://localhost:3216":
            raise ValueError("isolated test origin must be localhost:3216")
        allowed_origins.add(test_origin)
    (home / "logs").mkdir(exist_ok=True, mode=0o700)
    logger = logging.getLogger("robo")
    if not logger.handlers:
        handler = RotatingFileHandler(home / "logs" / "app.log", maxBytes=2 * 1024 * 1024, backupCount=3)
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    os.chmod(home / "logs" / "app.log", 0o600) if (home / "logs" / "app.log").exists() else None

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(runtime.run()) if collect else None
        discovery_task = asyncio.create_task(discovery.run()) if collect else None
        try:
            yield
        finally:
            for pending in (task, discovery_task):
                if pending:
                    pending.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await pending
            if not task:
                await asyncio.gather(*(a.close() for a in runtime.adapters))

    app = FastAPI(title="Robo Arbitrage", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.runtime, app.state.auth, app.state.store = runtime, auth, store
    app.state.discovery = discovery
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", os.environ.get("ROBO_PUBLIC_HOST", "example.invalid")])

    @app.middleware("http")
    async def security(request, call_next):
        ip = request.client.host if request.client else "unknown"
        if not auth.permit("all:" + ip, 180, 60):
            return JSONResponse({"detail": "rate limited"}, 429, headers={"Retry-After": "60"})
        session = auth.session(request.cookies.get("robo_session"))
        public = request.url.path in {
            "/",
            "/health/live",
            "/auth/login",
            "/assets/app.js",
            "/assets/style.css",
            "/assets/phase2.js",
            "/assets/discovery.js",
        }
        if not public and not session:
            return JSONResponse({"detail": "authentication required"}, 401)
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            if request.headers.get("origin") not in allowed_origins:
                return JSONResponse({"detail": "origin denied"}, 403)
            if request.headers.get("content-type", "").split(";")[0] != "application/json":
                return JSONResponse({"detail": "JSON required"}, 415)
            try:
                size = int(request.headers.get("content-length", "-1"))
            except ValueError:
                size = -1
            if not 0 <= size <= 4096:
                return JSONResponse({"detail": "body length required, max 4096"}, 413)
            chunks, received = [], 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > 4096:
                    return JSONResponse({"detail": "body too large"}, 413)
                chunks.append(chunk)
            request._body = b"".join(chunks)
            if request.url.path != "/auth/login" and (
                not session
                or not hmac.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"])
            ):
                return JSONResponse({"detail": "CSRF denied"}, 403)
        request.state.session = session
        response = await call_next(request)
        return response

    @app.middleware("http")
    async def hardening(request, call_next):
        if len(request.scope.get("raw_path", b"")) > 4096:
            response = JSONResponse({"detail": "URI too long"}, 414)
        elif request.method not in {"GET", "POST", "HEAD", "OPTIONS"}:
            response = JSONResponse(
                {"detail": "method denied"}, 405, headers={"Allow": "GET, POST, HEAD, OPTIONS"}
            )
        else:
            response = await call_next(request)
        response.headers.update(
            {
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Cache-Control": "no-store",
                "Referrer-Policy": "no-referrer",
                "Content-Security-Policy": "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'; object-src 'none'",
                "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
            }
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, exc):
        return JSONResponse({"detail": str(exc)}, 409)

    @app.get("/")
    async def index():
        return FileResponse(Path(__file__).resolve().parents[1] / "web" / "index.html")

    @app.get("/assets/{asset}")
    async def assets(asset: Literal["app.js", "style.css", "phase2.js", "discovery.js"]):
        return FileResponse(Path(__file__).resolve().parents[1] / "web" / asset)

    @app.get("/health/live")
    async def live():
        return {"alive": True, "mode": "PAPER_ONLY", "real_trading": "NOT_IMPLEMENTED", "version": version}

    @app.get("/health/ready")
    async def ready():
        overview = runtime.overview()
        count = sum(p["fresh_symbols"] for p in overview["providers"])
        return JSONResponse(
            {"ready": count == 6, "fresh_symbols": count, "required": 6, "last_cycle": runtime.last_cycle},
            status_code=200 if count == 6 else 503,
        )

    @app.post("/auth/login")
    async def login(payload: Login, request: Request):
        if not auth.permit("login:" + (request.client.host if request.client else "unknown"), 5, 300):
            raise HTTPException(429, "login rate limited", headers={"Retry-After": "300"})
        result = auth.login(payload.username, payload.password)
        if not result:
            raise HTTPException(401, "invalid credentials")
        token, session = result
        response = JSONResponse({"csrf": session["csrf"], "username": "admin"})
        response.set_cookie(
            "robo_session",
            token,
            httponly=True,
            secure=request.url.scheme == "https",
            samesite="strict",
            max_age=28800,
            path="/",
        )
        store.event("auth_login", {"user": "admin"})
        return response

    @app.get("/auth/session")
    async def session(request: Request):
        return {"csrf": request.state.session["csrf"], "username": "admin"}

    @app.post("/auth/logout")
    async def logout(request: Request):
        auth.logout(request.cookies.get("robo_session"))
        response = JSONResponse({"logged_out": True})
        response.delete_cookie("robo_session", path="/")
        return response

    @app.get("/api/overview")
    async def overview():
        return runtime.overview()

    @app.get("/api/history/{venue}/{symbol}")
    async def history(venue: Literal["bybit", "bitget"], symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]):
        rows = store.snapshots(venue, symbol, 300)
        return {
            "snapshots": [
                {
                    "ts": q["collected_at"],
                    "mark": q["mark"],
                    "index": q["index"],
                    "basis_pct": 100 * (q["mark"] / q["index"] - 1),
                    "funding_rate": q["funding_rate"],
                }
                for q in rows
            ],
            "settlements": store.history(venue, symbol),
            "source": "public API; prospective books",
        }

    @app.get("/api/monitor")
    async def monitor():
        result = runtime.monitor.summary(now_ms())
        if result["latest"]:
            result["latest"].pop("quotes", None)
            result["latest"]["quotes_omitted_from_summary"] = True
        return result

    @app.get("/api/monitor/{window}")
    async def monitor_detail(window: int):
        result = runtime.monitor.detail(window)
        if result is None:
            raise HTTPException(404, "decision window not found")
        return result

    @app.get("/api/live-gate")
    async def research_live_gate():
        return live_gate(store)

    @app.get("/api/discovery")
    async def public_market_discovery(budget: float = Query(default=1000, ge=5, le=10000)):
        try:
            result = await asyncio.to_thread(discovery_overview, home, budget)
        except (sqlite3.Error, OSError, ValueError, EOFError):
            return JSONResponse({"ready": False, "live": False, "detail": "Discovery evidence unavailable or failed integrity"}, 503)
        return result | {"worker": {"running": discovery.running, "last_error": discovery.last_error,
                                     "last_cycle": discovery.last_cycle}, "configured_scenarios_brl": discovery.budgets}

    @app.get("/api/discovery/history")
    def public_market_history(limit: int = Query(default=24, ge=1, le=100)):
        try:
            return {"runs": discovery_history(home, limit), "live": False}
        except (sqlite3.Error, OSError, ValueError):
            raise HTTPException(503, "Discovery history unavailable") from None

    @app.get("/api/discovery/history/{rid}")
    def public_market_detail(rid: str):
        if len(rid) != 32 or any(c not in "0123456789abcdef" for c in rid):
            raise HTTPException(422, "Invalid run id")
        try:
            result = discovery_detail(home, rid)
        except (sqlite3.Error, OSError, ValueError, EOFError):
            raise HTTPException(503, "Discovery evidence failed integrity") from None
        if result is None:
            raise HTTPException(404, "Discovery run not found")
        return result

    @app.post("/api/discovery/scenarios")
    async def configure_discovery_scenarios(body: DiscoveryScenarios):
        discovery.configure(body.budgets_brl)
        return {"budgets_brl": discovery.budgets, "live": False, "auto_open": False,
                "notice": "Virtual budgets only; applies to next cycle, no ledger changes"}

    @app.get("/api/data/coverage")
    async def data_coverage():
        return runtime.data_coverage()

    @app.get("/api/data/funding/{venue}/{symbol}")
    async def historical_funding(
        venue: Literal["bybit", "bitget"], symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    ):
        with store.connect() as db:
            rows = [
                dict(r)
                for r in db.execute(
                    "SELECT ts,rate,observed,source,payload_sha256 FROM historical_funding WHERE venue=? AND symbol=? ORDER BY ts LIMIT 2000",
                    (venue, symbol),
                )
            ]
        return {"venue": venue, "symbol": symbol, "rows": rows, "kind": "REALIZED_FUNDING_NOT_EXECUTION_PNL"}

    @app.get("/api/data/stress/{venue}/{symbol}")
    async def historical_stress(
        venue: Literal["bybit", "bitget"], symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"]
    ):
        return funding_stress(store, venue, symbol)

    @app.get("/api/data/snapshots/{venue}/{symbol}")
    async def prospective_query(
        venue: Literal["bybit", "bitget"],
        symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"],
        start: int = Query(ge=0),
        end: int = Query(ge=0),
        limit: int = Query(default=100, ge=1, le=500),
    ):
        if end < start or end - start > 35 * 86400000:
            raise HTTPException(422, "range limited to 35 days")
        with store.connect() as db:
            hot = [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT body FROM snapshots WHERE venue=? AND symbol=? AND ts>=? AND ts<=? ORDER BY ts LIMIT ?",
                    (venue, symbol, start, end, limit),
                )
            ]
        cold = archived_query(store, venue, symbol, start, end, limit)
        return {
            "rows": sorted(cold + hot, key=lambda q: q["collected_at"])[:limit],
            "kind": "OBSERVED_L2_HOT_PLUS_LOSSLESS_ARCHIVE",
            "limit": limit,
        }

    @app.get("/api/data/export")
    async def data_export(
        kind: Literal["funding", "candles", "decisions"],
        venue: Literal["bybit", "bitget"] = "bybit",
        symbol: Literal["BTCUSDT", "ETHUSDT", "SOLUSDT"] = "BTCUSDT",
        market: Literal["spot", "perp"] = "spot",
        limit: int = Query(default=1000, ge=1, le=10000),
    ):
        with store.connect() as db:
            if kind == "funding":
                rows = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM historical_funding WHERE venue=? AND symbol=? ORDER BY ts LIMIT ?",
                        (venue, symbol, limit),
                    )
                ]
            elif kind == "candles":
                rows = [
                    dict(r)
                    for r in db.execute(
                        "SELECT * FROM candles WHERE market=? AND symbol=? ORDER BY ts LIMIT ?",
                        (market, symbol, limit),
                    )
                ]
            else:
                latest = runtime.monitor.summary(now_ms())["latest"]
                rows = latest["decisions"] if latest else []
        output = io.StringIO()
        columns = sorted({k for r in rows for k in r}) if rows else ["no_data"]
        writer = csv.DictWriter(output, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            safe = {
                k: ("'" + str(v) if isinstance(v, str) and v[:1] in {"=", "+", "-", "@"} else v)
                for k, v in row.items()
            }
            writer.writerow(safe)
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="robo-v02-{kind}.csv"'},
        )

    @app.get("/api/events")
    async def events():
        return store.events()

    @app.post("/api/paper/open")
    async def paper_open(payload: Open):
        q = runtime.quotes.get(payload.venue + ":" + payload.symbol)
        if not q:
            raise HTTPException(409, "NO_DATA")
        pos = runtime.paper.open(
            q, payload.notional, now_ms(), payload.request_id, payload.research, payload.scenario
        )
        store.event("paper_open", {"id": pos["id"], "status": pos["status"]})
        return pos

    @app.post("/api/paper/close/{pid}")
    async def paper_close(pid: str):
        state = runtime.paper.state()
        p = next((p for p in state["positions"] if p["id"] == pid), None)
        if not p:
            raise HTTPException(404, "position not found")
        q = runtime.quotes.get(p["venue"] + ":" + p["symbol"])
        if not q:
            raise HTTPException(409, "NO_DATA")
        result = runtime.paper.close(pid, q, now_ms())
        store.event("paper_close", {"id": pid, "status": result["status"]})
        return result

    @app.post("/api/paper/halt")
    async def halt(payload: Halt):
        store.event("paper_halt", {"halted": payload.halted})
        return runtime.paper.halt(payload.halted)

    @app.get("/api/experiments")
    async def experiments():
        return store.experiments()

    @app.post("/api/experiments")
    async def experiment(payload: Market):
        if not auth.permit("experiment", 2, 60):
            raise HTTPException(429, "experiment rate limited")
        version_path = home / "VERSION"
        version = (
            (version_path.read_text().strip() if version_path.exists() else "development") + "@" + code_commit
        )
        return run_research(store, payload.venue, payload.symbol, payload.notional, version)

    @app.get("/api/export/{kind}")
    async def export(kind: Literal["funding", "paper", "observations"]):
        if kind == "funding":
            rows = [
                e
                for v in ["bybit", "bitget"]
                for s in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
                for e in store.history(v, s)
            ]
        elif kind == "paper":
            rows = runtime.paper.state()["positions"]
        else:
            rows = runtime.overview()["opportunities"]
        output = io.StringIO()
        columns = list(rows[0]) if rows else ["no_data"]
        writer = csv.DictWriter(output, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            # No spreadsheet formula injection on downloaded textual fields.
            safe = {
                k: ("'" + str(v) if isinstance(v, str) and v[:1] in {"=", "+", "-", "@"} else v)
                for k, v in row.items()
            }
            writer.writerow(safe)
        return Response(
            output.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="robo-{kind}.csv"'},
        )

    return app


app = create_app()

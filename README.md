# Robo Arbitrage — Public Research Edition

Open research prototype for cryptocurrency arbitrage, using **real public market observations** and **virtual trades only**.

**Status:** educational / paper-only / archived October 2026. No real-order execution, wallet access or exchange API credentials. **Not investment advice; no demonstrated profitable trading strategy.**

## What is included
- Python/FastAPI dashboard and API with session authentication.
- Market data collectors and historical retention using SQLite, public HTTP data and CCXT.
- Spot/perpetual (same venue), spot/spot (cross venue), and perpetual/perpetual (funding differential) **counterfactual** strategies.
- Independent virtual accounts, BRL simulation budgets, quality gates, paper ledger and audit trail.
- Tests, research scripts, publicly distributable technical reports, and notices for referenced upstream projects.

The research inventoried **104 CCXT adapter IDs** in October 2026 (not 104 continuously supported exchanges). Initial research monitored Bybit/Bitget plus five more venues: OKX, KuCoin, Binance, BingX, Gate. Exchange eligibility and availability may change; the October 2026 observations are not live endorsements.

**Known limitation:** research did not establish any consistently executable, net-profitable arbitrage opportunities; models reject missing timestamp, balance, margin, fee or settlement data rather than inventing assumptions. Never treat simulated performance as evidence of achievable fills or profits.

## Local setup
Requires Python 3.12+ (and optionally Node.js/Playwright for browser E2E). Python dependencies are listed in `requirements.lock`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
.venv/bin/python -m pip install pytest ruff
.venv/bin/python -m pytest -q tests
ROBO_HOME=. .venv/bin/python -m uvicorn robo.app:app --host 127.0.0.1 --port 3215
```

The web UI needs local authentication/bootstrap configuration before use; see `robo/security.py` and `scripts/change-password.py`. It is not intended to be deployed on an Internet-facing host without independent security review.

Default trusted host/origin is `example.invalid` plus localhost. Set `ROBO_PUBLIC_ORIGIN` and `ROBO_PUBLIC_HOST` to match your own deployment before exposing a reverse proxy. **Do not copy production deployment scripts into a running system without reviewing them first.**

## Source vs original installation
This is an intentionally curated, **fresh-history** public snapshot. It excludes the original private Git history, real databases, logs, production raw order-book archives (except one public-market offline test fixture), screenshots, staging captures, server deployment files, IP addresses, credentials and private environment configuration. Historical experimental reports are retained only where suitable for publication; their published timestamps refer to October 2026.

Scripts under `scripts/` include historical operational tooling and may require adjustments to local paths or staging data; they are not a one-command deployment recipe.

## Safety
- Never use real funds with this version.
- No real trading engine is implemented.
- Experimental calculations, funding rates and USDT/BRL conversions may be incomplete or inaccurate.
- Review terms of service, exchange API rate limits and applicable laws independently.

## Licensing
Third-party license/attribution notices for researched libraries or frameworks appear under `docs/licenses/`. This repository does **not** grant a separate open-source license to its original code; public visibility alone is not permission to redistribute or reuse under an open-source license. The repository owner can choose and add a license separately.

"""One bounded public-market scan. No order APIs, no credentials."""

import asyncio
import argparse
import json
from pathlib import Path
from robo.discovery_scanner import Scanner, DEFAULT_SCENARIOS

if __name__ == "__main__":
    home = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--budgets', type=float, nargs='+', default=DEFAULT_SCENARIOS)
    args = parser.parse_args()
    payload = asyncio.run(Scanner(home, args.budgets).cycle())
    print(json.dumps({'id': payload['id'], 'health': payload['health'],
                      'observations': len(payload['samples']), 'decisions': len(payload['decisions']),
                      'fills': payload['fills'], 'real_pnl': payload['real_pnl'], 'live': payload['live']}))

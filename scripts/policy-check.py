"""Small scoped safety gate, not a substitute for a full security audit."""

import ast
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from robo.adapters import PATHS, public_request_allowed

root = Path(__file__).resolve().parents[1]
for path in (root / "robo").glob("*.py"):
    tree = ast.parse(path.read_text(), filename=str(path))
    for call in ast.walk(tree):
        if not isinstance(call, ast.Call):
            continue
        name = getattr(call.func, "attr", getattr(call.func, "id", ""))
        assert name not in {
            "eval",
            "exec",
            "withdraw",
            "transfer",
            "create_order",
            "create_orders",
            "cancel_order",
            "cancel_all_orders",
            "create_market_order",
            "set_leverage",
        }, (path, name)
        assert not any(
            k.arg == "shell" and isinstance(k.value, ast.Constant) and k.value.value is True
            for k in call.keywords
        ), path
for venue, paths in PATHS.items():
    for path in paths:
        url = "https://api." + venue + ".com" + path
        assert public_request_allowed(venue, url, "GET")
        assert not public_request_allowed(venue, url, "POST")
        assert not public_request_allowed(venue, url.replace("https://", "http://"), "GET")
assert not public_request_allowed("bybit", "https://api.bybit.com/v5/order/create", "POST")
assert "live" not in {p.name for p in (root / "robo").glob("*.py")}
print(
    "Scoped policy assertions PASS: no financial execution call sites, no shell=True/eval/exec; transport denies non-public capability. Not a complete security audit."
)

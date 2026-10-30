"""Read-only sanitized deployment inventory. No environment or credentials exported."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time


def run(args):
    r = subprocess.run(args, capture_output=True, text=True, timeout=20, check=False)  # noqa: S603
    return {"code": r.returncode, "output": r.stdout + r.stderr}


out = {
    "at": time.time(),
    "ports": run(["ss", "-lnt"]),
    "resources": run(["free", "-m"]),
    "disk": run(["df", "-h", "/"]),
    "nginx_test": run(["sudo", "-n", "nginx", "-t"]),
}
pm = run(["pm2", "jlist"])
try:
    out["pm2"] = [
        {"name": p["name"], "pid": p["pid"], "status": p["pm2_env"]["status"]}
        for p in json.loads(pm["output"])
    ]
except (ValueError, KeyError):
    out["pm2"] = {"error": "could not parse sanitized process inventory"}
out["nginx_files"] = {}
for directory in [Path("/etc/nginx/sites-available"), Path("/etc/nginx/sites-enabled")]:
    for p in directory.iterdir():
        if p.name == "example.invalid":
            continue
        if p.is_symlink():
            out["nginx_files"][str(p)] = {"target": os.readlink(p)}
        elif p.is_file():
            try:
                digest = hashlib.sha256(p.read_bytes()).hexdigest()
            except PermissionError:
                digest = run(["sudo", "-n", "sha256sum", str(p)])["output"].split()[0]
            out["nginx_files"][str(p)] = {"sha256": digest}
for resolver in ["1.1.1.1", "8.8.8.8", "lunar.dns-parking.com", "solar.dns-parking.com"]:
    out["dns_" + resolver] = run(
        ["dig", "example.invalid", "A", "@" + resolver, "+noall", "+answer", "+comments"]
    )
Path(sys.argv[1]).write_text(json.dumps(out, indent=2))
print(
    "Saved sanitized inventory", sys.argv[1], "pm2", len(out["pm2"]), "nginx configs", len(out["nginx_files"])
)

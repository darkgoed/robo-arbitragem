import json
from datetime import datetime, timezone
from pathlib import Path

root = Path(__file__).resolve().parents[1]
before = json.loads((root / "evidence/pre-deploy.json").read_text())
after = json.loads((root / "evidence/post-deploy.json").read_text())
report = {
    "at": datetime.now(timezone.utc).isoformat(),
    "nginx_configs_compared": len(after["nginx_files"]),
    "other_nginx_unchanged": before["nginx_files"] == after["nginx_files"],
    "pm2_count": len(after["pm2"]),
    "pm2_pid_status_unchanged": before["pm2"] == after["pm2"],
    "scope": "Only own systemd unit, vhost and certificate added. Inventory is not exhaustive proof of zero performance impact.",
}
(root / "evidence/noninterference.json").write_text(json.dumps(report, indent=2))
print(json.dumps(report))
assert report["other_nginx_unchanged"] and report["pm2_pid_status_unchanged"]

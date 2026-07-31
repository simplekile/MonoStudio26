"""Fusion Saver end-render Discord notify — deployed per project under .monostudio/fusion/."""

from __future__ import annotations

import getpass
import json
import os
import socket
import sys
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path


def _script_dir() -> Path:
    return Path(__file__).resolve().parent


def _load_webhook_urls() -> list[str]:
    base = _script_dir()
    urls: list[str] = []
    seen: set[str] = set()

    def _add(url: str) -> None:
        u = (url or "").strip()
        if not u or u in seen:
            return
        seen.add(u)
        urls.append(u)

    webhooks_json = base / "webhooks.json"
    if webhooks_json.is_file():
        try:
            raw = json.loads(webhooks_json.read_text(encoding="utf-8"))
            if isinstance(raw, list):
                for item in raw:
                    if isinstance(item, str):
                        _add(item)
                    elif isinstance(item, dict):
                        _add(str(item.get("url") or ""))
        except (OSError, json.JSONDecodeError):
            pass

    legacy = base / "webhook.url"
    try:
        for line in legacy.read_text(encoding="utf-8").splitlines():
            _add(line)
    except OSError:
        pass
    return urls


WEBHOOKS = _load_webhook_urls()

render_file = sys.argv[1] if len(sys.argv) > 1 else "Unknown"
saver_name = sys.argv[2] if len(sys.argv) > 2 else "Unknown"
comp_name = sys.argv[3] if len(sys.argv) > 3 else "Unknown"

if not WEBHOOKS:
    try:
        (_script_dir() / "discord_notify.log").write_text(
            f"{datetime.now().isoformat()} skipped: no webhooks in webhooks.json / webhook.url\n",
            encoding="utf-8",
        )
    except OSError:
        pass
    sys.exit(0)

computer = socket.gethostname()
user = getpass.getuser()

folder = os.path.dirname(render_file)
filename = os.path.basename(render_file)

embed = {
    "title": "✅ Fusion Render Finished",
    "color": 0x2ECC71,
    "fields": [
        {"name": "🖥 Machine", "value": computer, "inline": True},
        {"name": "👤 User", "value": user, "inline": True},
        {"name": "📂 Composition", "value": comp_name, "inline": False},
        {"name": "💾 Saver", "value": saver_name, "inline": True},
        {"name": "📄 File", "value": filename, "inline": False},
        {"name": "📁 Folder", "value": f"```{folder}```", "inline": False},
        {"name": "🕒 Finished", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "inline": True},
    ],
    "footer": {"text": "MonoStudio Fusion Notify"},
}

payload = {"username": "Fusion", "embeds": [embed]}
body = json.dumps(payload).encode("utf-8")

for webhook in WEBHOOKS:
    req = urllib.request.Request(
        webhook,
        data=body,
        headers={"Content-Type": "application/json", "User-Agent": "FusionNotify"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req) as resp:
            print(resp.status)
    except urllib.error.HTTPError as e:
        print(e.code)
        print(e.read().decode())
    except Exception as e:
        print(e)

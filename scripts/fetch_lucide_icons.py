"""Fetch Lucide SVGs into monostudio_data/icons/lucide/."""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "monostudio_data" / "icons" / "lucide"

ICONS = {
    "sync": "https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/cloud-check.svg",
    "local": "https://raw.githubusercontent.com/lucide-icons/lucide/main/icons/hard-drive.svg",
}


def _fetch(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "MonoStudio/1.0"})
    return urllib.request.urlopen(req, timeout=30).read().decode("utf-8")


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, url in ICONS.items():
        data = _fetch(url)
        if "currentColor" not in data:
            data = data.replace('stroke="#000"', 'stroke="currentColor"').replace(
                'stroke="black"', 'stroke="currentColor"'
            )
        path = OUT / f"{name}.svg"
        path.write_text(data, encoding="utf-8")
        print(f"wrote {path.name} ({len(data)} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

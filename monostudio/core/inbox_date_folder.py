"""
Inbox / Outbox date folder naming — DDMMYY_<suffix> (e.g. 260515_Stb).

Legacy folders using YYYY-MM-DD remain readable and sortable.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime
from pathlib import Path

_LEGACY_ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATE_FOLDER_RE = re.compile(r"^(\d{6})_([A-Za-z0-9]+)$")


def sanitize_date_folder_suffix(suffix: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]", "", (suffix or "").strip())
    if not s:
        return "Prj"
    return s[:6]


def format_date_folder_name(d: date, suffix: str) -> str:
    tag = sanitize_date_folder_suffix(suffix)
    return f"{d.strftime('%d%m%y')}_{tag}"


def parse_date_folder_name(name: str) -> date | None:
    key = (name or "").strip()
    if not key:
        return None
    if _LEGACY_ISO_RE.match(key):
        try:
            return datetime.strptime(key, "%Y-%m-%d").date()
        except ValueError:
            return None
    m = _DATE_FOLDER_RE.match(key)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d%m%y").date()
    except ValueError:
        return None


def date_folder_sort_key(name: str) -> tuple[date, str]:
    parsed = parse_date_folder_name(name)
    if parsed is not None:
        return (parsed, name)
    return (date.min, name)


def default_date_folder_suffix(project_root: Path | None) -> str:
    if project_root is None:
        return "Prj"
    manifest = Path(project_root) / ".monostudio" / "project.json"
    try:
        if manifest.is_file():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            name = (data.get("name") or "").strip()
            letters = re.sub(r"[^A-Za-z]", "", name)
            if len(letters) >= 3:
                return letters[:3]
            if letters:
                return letters
    except (OSError, json.JSONDecodeError, TypeError):
        pass
    return "Prj"


def resolve_date_folder_name(
    folder_name: str | None,
    *,
    project_root: Path | None = None,
    suffix: str | None = None,
    on_date: date | None = None,
) -> str:
    """Return a safe inbox/outbox date folder name."""
    if folder_name and folder_name.strip():
        return folder_name.strip()
    d = on_date or date.today()
    tag = suffix if suffix is not None else default_date_folder_suffix(project_root)
    return format_date_folder_name(d, tag)

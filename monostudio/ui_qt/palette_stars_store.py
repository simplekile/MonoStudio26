"""Starred items for Command Palette — persist per project, prioritize when palette opens."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QSettings

_SETTINGS_KEY = "ui/palette_stars"
_MAX_STARS = 48


def _norm_root(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path).strip()


def _norm_path(path: str | Path | None) -> str:
    if not path:
        return ""
    try:
        return str(Path(path).resolve())
    except OSError:
        return str(path).strip()


@dataclass
class PaletteStarEntry:
    kind: str  # entity | inbox
    path: str
    project_root: str
    title: str
    subtitle: str = ""
    icon_name: str | None = None
    context: str = ""
    type_id: str = ""

    def storage_key(self) -> tuple[str, str, str]:
        return (_norm_root(self.project_root), self.kind, _norm_path(self.path))

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "path": self.path,
            "project_root": self.project_root,
            "title": self.title,
            "subtitle": self.subtitle,
            "icon_name": self.icon_name,
            "context": self.context,
            "type_id": self.type_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PaletteStarEntry | None:
        if not isinstance(data, dict):
            return None
        kind = (data.get("kind") or "").strip()
        path = (data.get("path") or "").strip()
        project_root = (data.get("project_root") or "").strip()
        title = (data.get("title") or "").strip()
        if kind not in ("entity", "inbox") or not path or not project_root or not title:
            return None
        icon = data.get("icon_name")
        return cls(
            kind=kind,
            path=path,
            project_root=project_root,
            title=title,
            subtitle=str(data.get("subtitle") or "").strip(),
            icon_name=icon.strip() if isinstance(icon, str) and icon.strip() else None,
            context=str(data.get("context") or "").strip(),
            type_id=str(data.get("type_id") or "").strip(),
        )


class PaletteStarsStore:
    """Ordered starred items (most recently starred first)."""

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings
        self._entries: list[PaletteStarEntry] = []
        self._load()

    def _load(self) -> None:
        raw = self._settings.value(_SETTINGS_KEY)
        self._entries = []
        if not raw:
            return
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
        except (json.JSONDecodeError, TypeError):
            return
        if not isinstance(data, list):
            return
        seen: set[tuple[str, str, str]] = set()
        for item in data:
            entry = PaletteStarEntry.from_dict(item) if isinstance(item, dict) else None
            if entry is None:
                continue
            key = entry.storage_key()
            if key in seen:
                continue
            seen.add(key)
            self._entries.append(entry)

    def _save(self) -> None:
        payload = [e.to_dict() for e in self._entries[:_MAX_STARS]]
        self._settings.setValue(_SETTINGS_KEY, json.dumps(payload))

    def entries_for_project(self, project_root: str | Path | None) -> list[PaletteStarEntry]:
        root = _norm_root(project_root)
        if not root:
            return []
        return [e for e in self._entries if _norm_root(e.project_root) == root]

    def is_starred(self, project_root: str | Path | None, kind: str, path: str | Path) -> bool:
        root = _norm_root(project_root)
        k = (kind or "").strip()
        p = _norm_path(path)
        if not root or not k or not p:
            return False
        target = (root, k, p)
        return any(e.storage_key() == target for e in self._entries)

    def toggle(self, entry: PaletteStarEntry) -> bool:
        """Star or unstar. Returns True when item is starred after the call."""
        key = entry.storage_key()
        for i, existing in enumerate(self._entries):
            if existing.storage_key() == key:
                self._entries.pop(i)
                self._save()
                return False
        self._entries.insert(0, entry)
        if len(self._entries) > _MAX_STARS:
            self._entries = self._entries[:_MAX_STARS]
        self._save()
        return True

    def prune_missing_paths(self, project_root: str | Path | None) -> None:
        """Drop stars whose path no longer exists on disk."""
        root = _norm_root(project_root)
        if not root:
            return
        kept: list[PaletteStarEntry] = []
        changed = False
        for entry in self._entries:
            if _norm_root(entry.project_root) != root:
                kept.append(entry)
                continue
            if Path(entry.path).exists():
                kept.append(entry)
            else:
                changed = True
        if changed:
            self._entries = kept
            self._save()

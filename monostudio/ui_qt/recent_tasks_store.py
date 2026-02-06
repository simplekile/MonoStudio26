"""
Recent tasks store: app-level list of (project, item, department, dcc) opened recently.
Persisted via QSettings; dedupe by (project_root, item_path, department) with move-to-front.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from PySide6.QtCore import QSettings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _norm_path(p: Path | str) -> str:
    return str(Path(p).resolve()) if p else ""


class RecentTask:
    """One recent task entry (immutable view)."""

    __slots__ = ("project_root", "item_path", "item_name", "item_type", "department", "dcc", "opened_at")

    def __init__(
        self,
        *,
        project_root: str,
        item_path: str,
        item_name: str,
        item_type: Literal["asset", "shot"],
        department: str,
        dcc: str,
        opened_at: str,
    ) -> None:
        self.project_root = project_root
        self.item_path = item_path
        self.item_name = (item_name or "").strip() or Path(item_path).name
        self.item_type = item_type
        self.department = (department or "").strip()
        self.dcc = (dcc or "").strip()
        self.opened_at = opened_at

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_root": self.project_root,
            "item_path": self.item_path,
            "item_name": self.item_name,
            "item_type": self.item_type,
            "department": self.department,
            "dcc": self.dcc,
            "opened_at": self.opened_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecentTask | None:
        if not isinstance(data, dict):
            return None
        pr = data.get("project_root")
        ip = data.get("item_path")
        it = data.get("item_type")
        if pr is None or ip is None or it not in ("asset", "shot"):
            return None
        return cls(
            project_root=str(pr),
            item_path=str(ip),
            item_name=str(data.get("item_name", "")),
            item_type=it,
            department=str(data.get("department", "")),
            dcc=str(data.get("dcc", "")),
            opened_at=str(data.get("opened_at", _now_iso())),
        )


_MAX_RECENT = 30
_SETTINGS_KEY = "recent_tasks"


class RecentTasksStore:
    """
    In-memory list of recent tasks; persist/load via QSettings (JSON array).
    Dedupe by (project_root, item_path, department): re-open moves to front.
    """

    def __init__(self, settings: QSettings) -> None:
        self._settings = settings
        self._tasks: list[RecentTask] = []
        self._load()

    def _load(self) -> None:
        raw = self._settings.value(_SETTINGS_KEY)
        if not raw:
            self._tasks = []
            return
        if isinstance(raw, str):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                self._tasks = []
                return
        else:
            data = raw
        if not isinstance(data, list):
            self._tasks = []
            return
        self._tasks = []
        for entry in data:
            t = RecentTask.from_dict(entry) if isinstance(entry, dict) else None
            if t is not None:
                self._tasks.append(t)
        self._tasks = self._tasks[:_MAX_RECENT]

    def _save(self) -> None:
        arr = [t.to_dict() for t in self._tasks]
        self._settings.setValue(_SETTINGS_KEY, json.dumps(arr, ensure_ascii=False))

    def push(
        self,
        *,
        project_root: Path | str,
        item_path: Path | str,
        item_name: str,
        item_type: Literal["asset", "shot"],
        department: str,
        dcc: str,
    ) -> None:
        proot = _norm_path(project_root)
        ipath = _norm_path(item_path)
        if not proot or not ipath:
            return
        name = (item_name or "").strip() or Path(ipath).name
        now = _now_iso()
        new_task = RecentTask(
            project_root=proot,
            item_path=ipath,
            item_name=name,
            item_type=item_type,
            department=(department or "").strip(),
            dcc=(dcc or "").strip(),
            opened_at=now,
        )
        # Remove existing (project_root, item_path, department)
        self._tasks = [t for t in self._tasks if not (
            _norm_path(t.project_root) == proot
            and _norm_path(t.item_path) == ipath
            and (t.department or "").strip().lower() == (department or "").strip().lower()
        )]
        self._tasks.insert(0, new_task)
        self._tasks = self._tasks[:_MAX_RECENT]
        self._save()

    def get_all(self) -> list[RecentTask]:
        return list(self._tasks)

    def get_for_project(self, project_root: Path | None) -> list[RecentTask]:
        if project_root is None:
            return []
        proot = _norm_path(project_root)
        if not proot:
            return []
        return [t for t in self._tasks if _norm_path(t.project_root) == proot]

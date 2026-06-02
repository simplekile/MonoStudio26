"""In-memory schedule session: undo/redo and deferred disk writes."""

from __future__ import annotations

import copy
from datetime import datetime
from pathlib import Path

from monostudio.core.project_schedule import (
    SCHEDULE_FILENAME,
    ProjectSchedule,
    load_schedule_from_disk,
    schedules_equal,
    write_project_schedule_to_disk,
)


def read_active_schedule(project_root: Path) -> ProjectSchedule | None:
    doc = schedule_document_for_root(project_root)
    if doc is None:
        return None
    return clone_schedule(doc.schedule)


def write_active_schedule(
    project_root: Path,
    schedule: ProjectSchedule,
    *,
    record_undo: bool = True,
) -> bool:
    doc = schedule_document_for_root(project_root)
    if doc is None:
        return False
    if schedules_equal(doc.schedule, schedule):
        return True
    if record_undo:
        doc.begin_edit()
    doc.apply_schedule(schedule)
    return True

_MAX_UNDO = 50

_active_document: ScheduleDocument | None = None


def clone_schedule(schedule: ProjectSchedule) -> ProjectSchedule:
    return copy.deepcopy(schedule)


class ScheduleDocument:
    """Working copy of project schedule with undo/redo stacks."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = Path(project_root).resolve()
        self._schedule = ProjectSchedule()
        self._undo: list[ProjectSchedule] = []
        self._redo: list[ProjectSchedule] = []
        self._dirty = False
        self._last_saved_at: datetime | None = None

    @property
    def schedule(self) -> ProjectSchedule:
        return self._schedule

    @property
    def is_dirty(self) -> bool:
        return self._dirty

    @property
    def last_saved_at(self) -> datetime | None:
        return self._last_saved_at

    def _disk_mtime(self) -> datetime | None:
        path = self.project_root / ".monostudio" / SCHEDULE_FILENAME
        try:
            if path.is_file():
                return datetime.fromtimestamp(path.stat().st_mtime)
        except OSError:
            pass
        return None

    def load_from_disk(self) -> None:
        self._schedule = load_schedule_from_disk(self.project_root)
        self._undo.clear()
        self._redo.clear()
        self._dirty = False
        self._last_saved_at = self._disk_mtime()

    def begin_edit(self) -> None:
        self._undo.append(clone_schedule(self._schedule))
        if len(self._undo) > _MAX_UNDO:
            self._undo.pop(0)
        self._redo.clear()

    def apply_schedule(self, schedule: ProjectSchedule) -> None:
        if schedules_equal(self._schedule, schedule):
            return
        self._schedule = clone_schedule(schedule)
        self._dirty = True

    def replace_schedule(self, schedule: ProjectSchedule) -> None:
        """Undo/redo: replace working copy without recording undo."""
        self._schedule = clone_schedule(schedule)
        self._dirty = True

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    def undo(self) -> bool:
        if not self._undo:
            return False
        self._redo.append(clone_schedule(self._schedule))
        self._schedule = self._undo.pop()
        self._dirty = True
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self._undo.append(clone_schedule(self._schedule))
        self._schedule = self._redo.pop()
        self._dirty = True
        return True

    def save_now(self) -> None:
        write_project_schedule_to_disk(self.project_root, self._schedule)
        self._dirty = False
        self._last_saved_at = datetime.now()

    def save_if_dirty(self) -> bool:
        if not self._dirty:
            return False
        self.save_now()
        return True


def _roots_match(a: Path, b: Path) -> bool:
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return Path(a) == Path(b)


def active_schedule_document() -> ScheduleDocument | None:
    return _active_document


def activate_schedule_document(doc: ScheduleDocument | None) -> None:
    global _active_document
    _active_document = doc


def schedule_document_for_root(project_root: Path) -> ScheduleDocument | None:
    doc = _active_document
    if doc is None:
        return None
    if _roots_match(doc.project_root, project_root):
        return doc
    return None

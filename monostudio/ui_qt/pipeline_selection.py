# Shared multi-select state for Main View grid + list.

from __future__ import annotations

from pathlib import Path


def _path_key(path: Path) -> str:
    try:
        return str(path.resolve())
    except OSError:
        return str(path)


class PipelineSelectionStore:
    """Single source of truth for pipeline item selection by path."""

    def __init__(self) -> None:
        self._paths: set[str] = set()
        self._current: str | None = None
        self._anchor: str | None = None

    def clear(self) -> None:
        self._paths.clear()
        self._current = None
        self._anchor = None

    def paths(self) -> list[Path]:
        return [Path(p) for p in sorted(self._paths)]

    def path_set(self) -> set[str]:
        return set(self._paths)

    def current(self) -> Path | None:
        return Path(self._current) if self._current else None

    def anchor(self) -> Path | None:
        return Path(self._anchor) if self._anchor else None

    def count(self) -> int:
        return len(self._paths)

    def contains(self, path: Path) -> bool:
        return _path_key(path) in self._paths

    def set_single(self, path: Path | None) -> None:
        self._paths.clear()
        if path is None:
            self._current = None
            self._anchor = None
            return
        key = _path_key(path)
        self._paths.add(key)
        self._current = key
        self._anchor = key

    def set_current(self, path: Path | None) -> None:
        if path is None:
            self._current = None
            return
        key = _path_key(path)
        self._current = key
        if key in self._paths:
            self._anchor = key

    def toggle(self, path: Path) -> None:
        key = _path_key(path)
        if key in self._paths:
            self._paths.discard(key)
            if self._current == key:
                self._current = next(iter(self._paths), None)
        else:
            self._paths.add(key)
            self._current = key
        if self._current:
            self._anchor = self._current

    def select_many(self, paths: list[Path], *, current: Path | None = None) -> None:
        self._paths = {_path_key(p) for p in paths if p}
        if current is not None:
            ck = _path_key(current)
            self._current = ck if ck in self._paths else (next(iter(self._paths), None))
        elif self._current not in self._paths:
            self._current = next(iter(self._paths), None)
        if self._current:
            self._anchor = self._current

    def replace_paths(self, paths: set[str]) -> None:
        self._paths = set(paths)
        if self._current not in self._paths:
            self._current = next(iter(self._paths), None)
        if self._anchor not in self._paths:
            self._anchor = self._current

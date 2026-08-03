"""Per-row presentation cache — health + DCC badges precomputed off the paint path."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

from PySide6.QtGui import QIcon

from monostudio.core.dcc_registry import get_default_dcc_registry
from monostudio.core.models import Asset, Shot
from monostudio.core.dcc_status import resolve_dcc_status
from monostudio.ui_qt.brand_icons import brand_icon
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS

if TYPE_CHECKING:
    from monostudio.core.department_registry import DepartmentRegistry
    from monostudio.core.item_health import ItemHealth

DccBadge = tuple[QIcon | None, str, str]  # (icon | None, dcc_id, status)


@dataclass(frozen=True, slots=True)
class _HealthKey:
    path: str
    dept: str
    dcc_id: str


@dataclass(frozen=True, slots=True)
class _DccKey:
    path: str
    dept: str


def _norm(s: str) -> str:
    return (s or "").strip().casefold()


def compute_dcc_badges(
    ref: Asset | Shot,
    *,
    active_department: str | None,
    dept_registry: DepartmentRegistry | None,
) -> tuple[DccBadge, ...]:
    """Filesystem-driven DCC badges for grid/list (exists / creating)."""
    out: list[DccBadge] = []
    try:
        reg = get_default_dcc_registry()
    except Exception:
        return ()
    active_key = _norm(active_department or "")
    states = getattr(ref, "dcc_work_states", ()) or ()
    seen: set[tuple[str, str]] = set()

    def add_badge(dept_id: str, dcc_id: str, status: str) -> None:
        if (dept_id, dcc_id) in seen:
            return
        seen.add((dept_id, dcc_id))
        if status == "creating":
            out.append((None, dcc_id, "creating"))
            return
        if status != "exists":
            return
        try:
            info = reg.get_dcc_info(dcc_id) if dcc_id else None
        except Exception:
            info = None
        slug = info.get("brand_icon_slug") if isinstance(info, dict) else None
        color = info.get("brand_color_hex") if isinstance(info, dict) else None
        if isinstance(slug, str) and slug.strip():
            ic = brand_icon(slug.strip(), size=14, color_hex=(color if isinstance(color, str) else None))
        else:
            ic = lucide_icon("layers", size=14, color_hex=MONOS_COLORS["text_label"])
        out.append((ic, dcc_id, "exists"))

    for (dept_id, dcc_id), _state in states:
        dept_id = (dept_id or "").strip()
        dcc_id = (dcc_id or "").strip()
        if not dept_id or not dcc_id:
            continue
        if active_key and _norm(dept_id) != active_key:
            continue
        status = resolve_dcc_status(ref, dept_id, dcc_id)
        if status in ("exists", "creating"):
            add_badge(dept_id, dcc_id, status)
    for d in getattr(ref, "departments", ()) or ():
        dept_name = getattr(d, "name", "") or ""
        if active_key and _norm(dept_name) != active_key:
            continue
        if dept_registry is not None:
            dcc_ids = dept_registry.supported_dcc_ids(reg, dept_name)
        else:
            dcc_ids = reg.get_available_dccs(dept_name) or []
        for dcc_id in dcc_ids:
            dcc_id = (dcc_id or "").strip()
            if not dcc_id:
                continue
            status = resolve_dcc_status(ref, dept_name, dcc_id)
            if status == "creating":
                add_badge(dept_name, dcc_id, "creating")
    return tuple(out)


class PipelineRowPresentationCache:
    """Cache health + DCC badge lists keyed by path/dept/dcc."""

    def __init__(self) -> None:
        self._health: dict[_HealthKey, ItemHealth | None] = {}
        self._dcc: dict[_DccKey, tuple[DccBadge, ...]] = {}

    def clear(self) -> None:
        self._health.clear()
        self._dcc.clear()

    def invalidate_path(self, path: str) -> None:
        if not path:
            return
        try:
            from pathlib import Path

            key = str(Path(path).resolve())
        except OSError:
            key = path
        self._health = {k: v for k, v in self._health.items() if k.path != key}
        self._dcc = {k: v for k, v in self._dcc.items() if k.path != key}

    def invalidate_paths(self, paths: list[str]) -> None:
        for p in paths:
            self.invalidate_path(p)

    def health_for(
        self,
        ref: Asset | Shot,
        *,
        path: str,
        active_department: str | None,
        active_dcc_id: str | None,
    ) -> ItemHealth | None:
        dept = (active_department or "").strip()
        if not dept:
            return None
        try:
            from pathlib import Path

            path_key = str(Path(path).resolve())
        except OSError:
            path_key = path
        dcc_key = (active_dcc_id or "").strip()
        cache_key = _HealthKey(path=path_key, dept=dept, dcc_id=dcc_key)
        if cache_key in self._health:
            return self._health[cache_key]
        from monostudio.ui_qt.main_view import assess_view_item_health

        health = assess_view_item_health(ref, dept, active_dcc_id=active_dcc_id or None)
        self._health[cache_key] = health
        return health

    def dcc_badges_for(
        self,
        ref: Asset | Shot,
        *,
        path: str,
        active_department: str | None,
        dept_registry: DepartmentRegistry | None,
    ) -> tuple[DccBadge, ...]:
        dept = (active_department or "").strip()
        try:
            from pathlib import Path

            path_key = str(Path(path).resolve())
        except OSError:
            path_key = path
        cache_key = _DccKey(path=path_key, dept=dept)
        if cache_key in self._dcc:
            return self._dcc[cache_key]
        badges = compute_dcc_badges(
            ref,
            active_department=active_department,
            dept_registry=dept_registry,
        )
        self._dcc[cache_key] = badges
        return badges


def active_dcc_for_item(
    path: str | None,
    department: str | None,
    *,
    get_active_dcc: Callable[[str, str], str | None] | None,
) -> str | None:
    if not path or not (department or "").strip():
        return None
    if get_active_dcc is None:
        return None
    try:
        return get_active_dcc(path, department)
    except Exception:
        return None

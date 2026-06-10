"""
Per-department production status registries.

Departments may define their own workflow statuses (e.g. animation: layout → block →
spline → polish). When no department preset exists, falls back to the global
production status registry.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.production_status import (
    ProductionStatusRegistry,
    StatusDef,
    load_production_status_registry,
)

_log = logging.getLogger(__name__)

_DEPT_PRESETS_DIR = "department_status_presets"
_PROJECT_DEPT_STATUSES = Path(".monostudio") / "pipeline" / "department_statuses.json"


def _read_json_dict(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log.debug("department_status_registry: skip %s: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def _shipped_dept_preset_path(dept_id: str) -> Path:
    return (
        get_app_base_path()
        / "monostudio_data"
        / "pipeline"
        / _DEPT_PRESETS_DIR
        / f"{(dept_id or '').strip()}.json"
    )


def _project_dept_statuses_path(project_root: Path) -> Path:
    return Path(project_root) / _PROJECT_DEPT_STATUSES


def _parse_statuses_list(raw: list | None) -> list[StatusDef]:
    out: list[StatusDef] = []
    if not isinstance(raw, list):
        return out
    for node in raw:
        if not isinstance(node, dict):
            continue
        sid = (node.get("id") or "").strip()
        if not sid:
            continue
        label = (node.get("label") or sid).strip() or sid
        cat = (node.get("category") or "not_started").strip() or "not_started"
        order_raw = node.get("order", node.get("rank"))
        try:
            order = int(order_raw) if order_raw is not None else 0
        except (TypeError, ValueError):
            order = 0
        tip_raw = node.get("tooltip")
        tip: str | None = None
        if isinstance(tip_raw, str) and tip_raw.strip():
            tip = tip_raw.strip()
        out.append(StatusDef(id=sid, label=label, category=cat, rank=order, tooltip=tip))
    return out


def _registry_from_preset_dict(data: dict) -> ProductionStatusRegistry | None:
    statuses = _parse_statuses_list(data.get("statuses"))
    if not statuses:
        return None
    cat_raw = data.get("category_order")
    if isinstance(cat_raw, list) and cat_raw:
        category_order = tuple(str(x).strip() for x in cat_raw if str(x).strip())
    else:
        category_order = (
            "blocked",
            "hold",
            "review",
            "in_progress",
            "not_started",
            "done",
            "na",
        )
    menu_raw = data.get("menu_category_order")
    menu_category_order: tuple[str, ...]
    if isinstance(menu_raw, list) and menu_raw:
        menu_category_order = tuple(str(x).strip() for x in menu_raw if str(x).strip())
    else:
        menu_category_order = category_order
    merged = {st.id: st for st in statuses}
    hidden: set[str] = set()
    hid = data.get("hidden_ids")
    if isinstance(hid, list):
        hidden.update(str(x).strip() for x in hid if str(x).strip())
    return ProductionStatusRegistry(
        category_order=category_order,
        menu_category_order=menu_category_order,
        statuses=merged,
        hidden_ids=frozenset(hidden),
    )


def department_has_custom_status_registry(project_root: Path | None, dept_id: str) -> bool:
    dep = (dept_id or "").strip()
    if not dep:
        return False
    if project_root is not None:
        overlay = _read_json_dict(_project_dept_statuses_path(Path(project_root)))
        if isinstance(overlay, dict):
            by_dept = overlay.get("by_department")
            if isinstance(by_dept, dict) and dep in by_dept:
                node = by_dept.get(dep)
                if isinstance(node, dict) and node.get("statuses"):
                    return True
    return _shipped_dept_preset_path(dep).is_file()


@lru_cache(maxsize=64)
def _cached_dept_registry(project_key: str, dept_id: str) -> ProductionStatusRegistry | None:
    """Load dept-specific registry without global fallback (cached)."""
    dep = (dept_id or "").strip()
    if not dep:
        return None
    project_root = Path(project_key) if project_key else None
    if project_root is not None:
        overlay = _read_json_dict(_project_dept_statuses_path(project_root))
        if isinstance(overlay, dict):
            by_dept = overlay.get("by_department")
            if isinstance(by_dept, dict):
                node = by_dept.get(dep)
                if isinstance(node, dict):
                    reg = _registry_from_preset_dict(node)
                    if reg is not None:
                        return reg
    shipped = _read_json_dict(_shipped_dept_preset_path(dep))
    if shipped:
        return _registry_from_preset_dict(shipped)
    return None


def load_status_registry_for_department(
    project_root: Path | None,
    dept_id: str,
) -> ProductionStatusRegistry:
    """Dept-specific registry, or global production status registry as fallback."""
    dep = (dept_id or "").strip()
    key = str(Path(project_root).resolve()) if project_root else ""
    if dep:
        custom = _cached_dept_registry(key, dep)
        if custom is not None:
            return custom
    return load_production_status_registry(project_root)


def status_workflow_order(registry: ProductionStatusRegistry, status_id: str) -> int:
    """Workflow position for goal comparison (higher = further along)."""
    sid = (status_id or "").strip()
    if not sid:
        return -1
    st = registry.get(sid)
    if st is None:
        return -1
    return int(st.rank)


def goal_is_met(
    current_status_id: str,
    target_status_id: str,
    registry: ProductionStatusRegistry,
) -> bool:
    """True when current status has reached or passed the target workflow stage."""
    target = (target_status_id or "").strip()
    if not target:
        return False
    current_order = status_workflow_order(registry, current_status_id)
    target_order = status_workflow_order(registry, target)
    if target_order < 0:
        return False
    if current_order < 0:
        return False
    return current_order >= target_order


def default_target_status_for_department(
    project_root: Path | None,
    dept_id: str,
) -> str:
    """Sensible default target for new schedule goals."""
    reg = load_status_registry_for_department(project_root, dept_id)
    visible = reg.menu_status_ids()
    if not visible:
        return "working"
    # Prefer last in-progress/review stage before done
    for cat in ("review", "in_progress", "done"):
        for sid in reversed(visible):
            if reg.category_for(sid) == cat:
                return sid
    return visible[-1]


def invalidate_department_status_cache() -> None:
    _cached_dept_registry.cache_clear()

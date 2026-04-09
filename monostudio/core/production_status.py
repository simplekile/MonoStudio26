"""
Production status: preset registry (defaults + project merge), computed/effective ids,
and aggregate for grid/Inspector.

UI maps category → color; labels come from registry (never raw id for display).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from monostudio.core.app_paths import get_app_base_path
from monostudio.core.item_status import read_item_status_overrides

if TYPE_CHECKING:
    from monostudio.core.models import Asset, Department, Shot

_log = logging.getLogger(__name__)

# Match MONOS_COLORS semantic usage in inspector/main_view
CATEGORY_COLOR_HEX: dict[str, str] = {
    "done": "#10b981",
    "in_progress": "#f59e0b",
    "not_started": "#71717a",
    "review": "#60a5fa",
    "hold": "#fbbf24",
    "blocked": "#ef4444",
    "na": "#52525b",
    "unknown": "#a1a1aa",
}

# Dot / list style keys (backward compatible with grid delegate)
CATEGORY_STYLE_KEY: dict[str, str] = {
    "done": "ready",
    "in_progress": "progress",
    "not_started": "waiting",
    "review": "review",
    "hold": "hold",
    "blocked": "blocked",
    "na": "na",
    "unknown": "waiting",
}

# Dropdown group order (workflow). category_order above stays for aggregate / priority logic.
DEFAULT_MENU_CATEGORY_ORDER: tuple[str, ...] = (
    "not_started",
    "in_progress",
    "review",
    "done",
    "hold",
    "blocked",
    "na",
)


@dataclass(frozen=True)
class StatusDef:
    id: str
    label: str
    category: str
    rank: int
    tooltip: str | None = None


@dataclass
class ProductionStatusRegistry:
    """Merged registry for one project (or defaults-only when project_root is None)."""

    category_order: tuple[str, ...]
    menu_category_order: tuple[str, ...]
    statuses: dict[str, StatusDef]
    hidden_ids: frozenset[str]

    def category_index(self, category: str) -> int:
        try:
            return self.category_order.index(category)
        except ValueError:
            return len(self.category_order)

    def known_status_id(self, status_id: str) -> bool:
        return (status_id or "").strip() in self.statuses

    def get(self, status_id: str) -> StatusDef | None:
        return self.statuses.get((status_id or "").strip())

    def menu_status_ids(self) -> list[str]:
        """Visible ids in UI order (JSON order, minus hidden)."""
        return [s.id for s in self.statuses.values() if s.id not in self.hidden_ids]

    def statuses_grouped_for_menu(self) -> list[tuple[str, list[str]]]:
        """(category_id, [status_id, ...]) in menu_category_order; sorted by rank within each group."""
        hidden = self.hidden_ids
        buckets: dict[str, list[StatusDef]] = {}
        for st in self.statuses.values():
            if st.id in hidden:
                continue
            cat = (st.category or "not_started").strip() or "not_started"
            buckets.setdefault(cat, []).append(st)
        for lst in buckets.values():
            lst.sort(key=lambda s: (s.rank, s.label.casefold()))
        ordered: list[str] = []
        for c in self.menu_category_order:
            if buckets.get(c):
                ordered.append(c)
        for c in sorted(buckets.keys(), key=str.casefold):
            if c not in ordered and buckets[c]:
                ordered.append(c)
        return [(c, [s.id for s in buckets[c]]) for c in ordered]

    def label_for(self, status_id: str) -> str:
        d = self.get(status_id)
        if d:
            return d.label
        sid = (status_id or "").strip()
        return sid or "Unknown"

    def category_for(self, status_id: str) -> str:
        d = self.get(status_id)
        if d:
            return d.category
        return "unknown"


def _default_presets_path() -> Path:
    return get_app_base_path() / "monostudio_data" / "pipeline" / "production_status_presets.json"


def _project_presets_path(project_root: Path) -> Path:
    return Path(project_root) / ".monostudio" / "pipeline" / "production_status_presets.json"


def _read_json_dict(path: Path) -> dict | None:
    try:
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        _log.debug("production_status: skip %s: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


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
        rank = node.get("rank")
        try:
            rnk = int(rank) if rank is not None else 0
        except (TypeError, ValueError):
            rnk = 0
        tip_raw = node.get("tooltip")
        tip: str | None = None
        if isinstance(tip_raw, str) and tip_raw.strip():
            tip = tip_raw.strip()
        out.append(StatusDef(id=sid, label=label, category=cat, rank=rnk, tooltip=tip))
    return out


def load_production_status_registry(project_root: Path | None) -> ProductionStatusRegistry:
    """
    Load defaults from monostudio_data, merge project overlay if present.
    """
    base = _read_json_dict(_default_presets_path()) or {}
    cat_raw = base.get("category_order")
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

    menu_category_order: tuple[str, ...] = DEFAULT_MENU_CATEGORY_ORDER
    menu_raw = base.get("menu_category_order")
    if isinstance(menu_raw, list) and menu_raw:
        menu_category_order = tuple(str(x).strip() for x in menu_raw if str(x).strip())

    merged: dict[str, StatusDef] = {}
    for st in _parse_statuses_list(base.get("statuses")):
        merged[st.id] = st

    hidden: set[str] = set()
    overlay: dict | None = None
    if project_root is not None:
        overlay = _read_json_dict(_project_presets_path(Path(project_root)))

    if overlay:
        hid = overlay.get("hidden_ids")
        if isinstance(hid, list):
            hidden.update(str(x).strip() for x in hid if str(x).strip())

        labels = overlay.get("labels")
        if isinstance(labels, dict):
            for k, v in labels.items():
                kid = (k or "").strip()
                if not kid or kid not in merged:
                    continue
                old = merged[kid]
                lab = (v or "").strip()
                if lab:
                    merged[kid] = StatusDef(
                        id=old.id,
                        label=lab,
                        category=old.category,
                        rank=old.rank,
                        tooltip=old.tooltip,
                    )

        tips_ov = overlay.get("tooltips")
        if isinstance(tips_ov, dict):
            for k, v in tips_ov.items():
                kid = (k or "").strip()
                if not kid or kid not in merged:
                    continue
                old = merged[kid]
                t = (v or "").strip() if isinstance(v, str) else ""
                if t:
                    merged[kid] = StatusDef(
                        id=old.id,
                        label=old.label,
                        category=old.category,
                        rank=old.rank,
                        tooltip=t,
                    )

        extras = overlay.get("extra_statuses")
        if isinstance(extras, list):
            for st in _parse_statuses_list(extras):
                merged[st.id] = st

        oc = overlay.get("category_order")
        if isinstance(oc, list) and oc:
            category_order = tuple(str(x).strip() for x in oc if str(x).strip())

        moc = overlay.get("menu_category_order")
        if isinstance(moc, list) and moc:
            menu_category_order = tuple(str(x).strip() for x in moc if str(x).strip())

    return ProductionStatusRegistry(
        category_order=category_order,
        menu_category_order=menu_category_order,
        statuses=merged,
        hidden_ids=frozenset(hidden),
    )


def computed_status_id_from_department(dept: Department) -> str:
    if dept.publish_version_count > 0:
        return "published"
    if dept.work_exists:
        return "working"
    return "waiting"


def effective_status_id_for_department(
    dept: Department,
    override_id: str | None,
    registry: ProductionStatusRegistry,
) -> str:
    computed = computed_status_id_from_department(dept)
    if override_id is None or not str(override_id).strip():
        return computed
    oid = str(override_id).strip()
    if registry.known_status_id(oid):
        return oid
    return oid


def _overrides_map(ref: Asset | Shot) -> dict[str, str]:
    raw = getattr(ref, "status_overrides", None) or ()
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items() if str(k).strip() and str(v).strip()}
    out: dict[str, str] = {}
    for pair in raw:
        if isinstance(pair, (tuple, list)) and len(pair) >= 2:
            k, v = pair[0], pair[1]
            if str(k).strip() and str(v).strip():
                out[str(k).strip()] = str(v).strip()
    return out


def override_status_id_for_department(ref: Asset | Shot, dept_name: str) -> str | None:
    d = (dept_name or "").strip()
    if not d:
        return None
    return _overrides_map(ref).get(d)


def department_has_status_override(ref: Asset | Shot, dept_name: str) -> bool:
    d = (dept_name or "").strip()
    if not d:
        return False
    return d in _overrides_map(ref)


def aggregate_status_id_for_item(
    ref: Asset | Shot,
    *,
    active_department: str | None,
    hidden_departments: set[str] | None,
    registry: ProductionStatusRegistry,
) -> str:
    """Single status_id for thumbnail dot when no per-dept chips (or aggregate)."""
    hidden = hidden_departments or set()
    overrides = _overrides_map(ref)
    depts = list(ref.departments)
    if not depts:
        return "waiting"

    dep_active = (active_department or "").strip()
    if dep_active:
        acf = dep_active.casefold()
        for d in depts:
            if (d.name or "").strip().casefold() == acf:
                dname = (d.name or "").strip()
                oid = overrides.get(dname)
                return effective_status_id_for_department(d, oid, registry)
        return "waiting"

    visible = [d for d in depts if (d.name or "") not in hidden]
    if not visible:
        visible = list(depts)

    d0 = visible[0]
    best_id = effective_status_id_for_department(d0, overrides.get(d0.name or ""), registry)
    best_cat_idx = registry.category_index(registry.category_for(best_id))
    best_rank = registry.get(best_id).rank if registry.get(best_id) else 0

    for d in visible[1:]:
        cid = effective_status_id_for_department(d, overrides.get(d.name or ""), registry)
        cat = registry.category_for(cid)
        cidx = registry.category_index(cat)
        rnk = registry.get(cid).rank if registry.get(cid) else 0
        if cidx < best_cat_idx:
            best_id, best_cat_idx, best_rank = cid, cidx, rnk
        elif cidx == best_cat_idx and rnk > best_rank:
            best_id, best_rank = cid, rnk
    return best_id


def effective_status_id_for_departments_list(
    ref: Asset | Shot,
    *,
    registry: ProductionStatusRegistry,
) -> list[tuple[str, str]]:
    """[(dept_name, effective_status_id), ...] in ref.departments order."""
    overrides = _overrides_map(ref)
    out: list[tuple[str, str]] = []
    for d in ref.departments:
        name = (d.name or "").strip()
        if not name:
            continue
        oid = overrides.get(name)
        out.append((name, effective_status_id_for_department(d, oid, registry)))
    return out


def style_key_for_status_id(status_id: str, registry: ProductionStatusRegistry) -> str:
    cat = registry.category_for(status_id)
    return CATEGORY_STYLE_KEY.get(cat, "waiting")


def color_hex_for_category(category: str) -> str:
    return CATEGORY_COLOR_HEX.get(category, CATEGORY_COLOR_HEX["unknown"])


def color_hex_for_status_id(status_id: str, registry: ProductionStatusRegistry) -> str:
    return color_hex_for_category(registry.category_for(status_id))


def load_status_overrides_for_scan(item_root: Path, department_names: tuple[str, ...]) -> tuple[tuple[str, str], ...]:
    """For fs_reader: frozen tuple of (dept_name, status_id)."""
    d = read_item_status_overrides(item_root, department_names)
    return tuple(sorted(d.items(), key=lambda x: x[0]))

from __future__ import annotations

import json
from pathlib import Path

from monostudio.core.atomic_write import atomic_write_text
from monostudio.core.app_paths import get_app_base_path
from monostudio.core.pipeline_types_and_presets import get_user_default_config_root

CREATE_DEFAULT_DCC_BY_DEPARTMENT_KEY = "create_default_dcc_by_department"


def _norm(s: str | None) -> str:
    return (s or "").strip().casefold()


def _mono2026_preset_path() -> Path:
    return get_app_base_path() / "monostudio_data" / "pipeline" / "department_presets" / "mono2026_preset.json"


def _parse_create_default_map(raw: object) -> dict[str, str]:
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if not isinstance(v, str) or not v.strip():
            continue
        out[k.strip()] = v.strip()
    return out


def shipped_create_default_dcc_map() -> dict[str, str]:
    """Factory defaults from mono2026_preset.json in the app bundle."""
    try:
        path = _mono2026_preset_path()
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        return _parse_create_default_map(data.get(CREATE_DEFAULT_DCC_BY_DEPARTMENT_KEY))
    except (OSError, json.JSONDecodeError):
        return {}


def user_default_create_default_dcc_map() -> dict[str, str]:
    """Optional override: Documents/.monostudio/pipeline/create_defaults.json."""
    path = get_user_default_config_root() / "pipeline" / "create_defaults.json"
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {}
        raw = data.get(CREATE_DEFAULT_DCC_BY_DEPARTMENT_KEY, data)
        return _parse_create_default_map(raw)
    except (OSError, json.JSONDecodeError):
        return {}


def resolved_create_default_dcc_map_for_new_project() -> dict[str, str]:
    """Shipped preset merged with user Documents override (user wins on key clash)."""
    merged = dict(shipped_create_default_dcc_map())
    merged.update(user_default_create_default_dcc_map())
    return merged


def _read_project_create_default_map(project_root: Path) -> dict[str, str]:
    path = Path(project_root) / ".monostudio" / "project.json"
    try:
        if not path.is_file():
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return _parse_create_default_map(data.get(CREATE_DEFAULT_DCC_BY_DEPARTMENT_KEY))


def read_create_default_dcc_map(project_root: Path) -> dict[str, str]:
    """
    Effective map: shipped preset < user Documents default < project.json override.
    """
    merged = dict(shipped_create_default_dcc_map())
    merged.update(user_default_create_default_dcc_map())
    merged.update(_read_project_create_default_map(project_root))
    return merged


def read_create_default_dcc(project_root: Path, department: str) -> str | None:
    """Resolve create-default DCC for a logical department (case-insensitive key match)."""
    dep_cf = _norm(department)
    if not dep_cf:
        return None
    for k, v in read_create_default_dcc_map(project_root).items():
        if _norm(k) == dep_cf:
            return v
    return None


def write_create_default_dcc_map(project_root: Path, mapping: dict[str, str]) -> bool:
    """Replace create-default map for the project (empty map clears project override only)."""
    path = Path(project_root) / ".monostudio" / "project.json"
    data: dict = {}
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                data = raw
    except (OSError, json.JSONDecodeError):
        data = {}
    merged = _parse_create_default_map(mapping)
    if merged:
        data[CREATE_DEFAULT_DCC_BY_DEPARTMENT_KEY] = merged
    else:
        data.pop(CREATE_DEFAULT_DCC_BY_DEPARTMENT_KEY, None)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        content = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        atomic_write_text(path, content, encoding="utf-8")
        return True
    except OSError:
        return False


def write_create_default_dcc(project_root: Path, department: str, dcc: str) -> bool:
    """Merge create-default DCC for department into project.json. Returns True on success."""
    dept = (department or "").strip()
    dcc_id = (dcc or "").strip()
    if not dept or not dcc_id:
        return False
    merged = _read_project_create_default_map(project_root)
    merged[dept] = dcc_id
    return write_create_default_dcc_map(project_root, merged)


def clear_create_default_dcc(project_root: Path, department: str) -> bool:
    """Remove create-default override for one department in project.json."""
    dept = (department or "").strip()
    if not dept:
        return False
    dep_cf = _norm(dept)
    merged = _read_project_create_default_map(project_root)
    out = {k: v for k, v in merged.items() if _norm(k) != dep_cf}
    if len(out) == len(merged):
        return True
    return write_create_default_dcc_map(project_root, out)

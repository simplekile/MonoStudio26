"""
MONOS ↔ Blender adapter (foundation only).

This module is the single entry point for Blender-side MONOS integration.
It intentionally provides a tiny, explicit API with clear extension points,
and avoids UI, menus, operators, publishing, and filesystem policy.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

try:
    import bpy  # type: ignore
except Exception:  # pragma: no cover
    bpy = None  # type: ignore

_DCC_NAME: str = "blender"
_SCENE_KEY_CONTEXT_JSON: str = "MONOS_CONTEXT_JSON"

EntityType = Literal["asset", "shot"]


class MonosContext(TypedDict):
    project_id: str
    entity_type: EntityType
    entity_id: str
    department: str
    dcc: Literal["blender"]


@dataclass(frozen=True)
class ValidatedContext:
    project_id: str
    entity_type: EntityType
    entity_id: str
    department: str
    dcc: str

    def as_dict(self) -> MonosContext:
        return {
            "project_id": self.project_id,
            "entity_type": self.entity_type,
            "entity_id": self.entity_id,
            "department": self.department,
            "dcc": "blender",
        }


def require_bpy() -> Any:
    if bpy is None:
        raise RuntimeError(
            "MONOS Blender adapter requires Blender's Python environment (bpy). "
            "This module must be executed from inside Blender."
        )
    return bpy


def _scene() -> Any:
    _bpy = require_bpy()
    scn = getattr(_bpy.context, "scene", None)
    if scn is None:
        raise RuntimeError("Blender context has no active scene; cannot store MONOS context.")
    return scn


def _validate_context(context: dict[str, Any]) -> ValidatedContext:
    if not isinstance(context, dict):
        raise RuntimeError("Invalid MONOS context: expected dict.")

    required = ("project_id", "entity_type", "entity_id", "department", "dcc")
    missing = [k for k in required if k not in context]
    if missing:
        raise RuntimeError(f"MONOS context is missing required keys: {', '.join(missing)}")

    project_id = context.get("project_id")
    entity_type = context.get("entity_type")
    entity_id = context.get("entity_id")
    department = context.get("department")
    dcc = context.get("dcc")

    for key, val in (
        ("project_id", project_id),
        ("entity_id", entity_id),
        ("department", department),
        ("dcc", dcc),
    ):
        if not isinstance(val, str) or not val.strip():
            raise RuntimeError(f"Invalid MONOS context: '{key}' must be a non-empty string.")

    if entity_type not in ("asset", "shot"):
        raise RuntimeError("Invalid MONOS context: 'entity_type' must be 'asset' or 'shot'.")

    if str(dcc).strip().lower() != _DCC_NAME:
        raise RuntimeError("Invalid MONOS context: 'dcc' must be 'blender'.")

    return ValidatedContext(
        project_id=str(project_id).strip(),
        entity_type=entity_type,  # type: ignore[assignment]
        entity_id=str(entity_id).strip(),
        department=str(department).strip(),
        dcc=_DCC_NAME,
    )


def _read_context_json() -> str | None:
    scn = _scene()
    if _SCENE_KEY_CONTEXT_JSON not in scn:
        return None
    raw = scn.get(_SCENE_KEY_CONTEXT_JSON)
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise RuntimeError("Stored MONOS context is corrupted: expected JSON string in scene custom property.")
    if not raw.strip():
        return None
    return raw


def _write_context_json(payload: str) -> None:
    scn = _scene()
    if not isinstance(payload, str) or not payload.strip():
        raise RuntimeError("Internal error: refusing to write empty MONOS context JSON.")
    scn[_SCENE_KEY_CONTEXT_JSON] = payload


def set_context(context: dict[str, Any]) -> None:
    """
    Persist MONOS context into the current Blender scene.

    The context is stored in scene custom properties and therefore survives save/reopen.
    This function does not guess or auto-fix missing fields.
    """
    validated = _validate_context(context)
    _write_context_json(json.dumps(validated.as_dict(), ensure_ascii=False, separators=(",", ":")))


def get_context() -> dict[str, Any] | None:
    """
    Return the current MONOS context dict from the scene, or None if not set.

    If the stored context exists but is invalid/corrupted, this fails loudly.
    """
    raw = _read_context_json()
    if raw is None:
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Stored MONOS context JSON is invalid: {e}") from e
    if not isinstance(data, dict):
        raise RuntimeError("Stored MONOS context JSON must decode to an object/dict.")
    _validate_context(data)
    return data


def ensure_context() -> None:
    """
    Validate that MONOS context exists and is well-formed.

    Raises RuntimeError if the context is missing or invalid.
    """
    ctx = get_context()
    if ctx is None:
        raise RuntimeError(
            "MONOS context is missing in this Blender file. "
            "Set it explicitly with set_context({...}) before saving/publishing."
        )
    _validate_context(ctx)


def open_file(filepath: str, context: dict[str, Any] | None = None) -> None:
    """
    Open a .blend file safely and optionally set MONOS context on the opened scene.

    If 'context' is provided, it will be applied AFTER opening the file (so it binds
    to the newly-loaded scene). No auto-fallback is performed.
    """
    _bpy = require_bpy()
    if not isinstance(filepath, str) or not filepath.strip():
        raise RuntimeError("open_file(filepath=...): filepath must be a non-empty string.")

    validated: ValidatedContext | None = None
    if context is not None:
        validated = _validate_context(context)

    result = _bpy.ops.wm.open_mainfile(filepath=filepath)
    if not isinstance(result, set) or "FINISHED" not in result:
        raise RuntimeError(f"Blender failed to open file: {filepath!r}")

    if validated is not None:
        _write_context_json(json.dumps(validated.as_dict(), ensure_ascii=False, separators=(",", ":")))


def save_file() -> None:
    """
    Save the current .blend file safely.

    This requires MONOS context to be present and valid, so saved files are never
    ambiguous from the pipeline's perspective.
    """
    _bpy = require_bpy()
    ensure_context()
    result = _bpy.ops.wm.save_mainfile()
    if not isinstance(result, set) or "FINISHED" not in result:
        raise RuntimeError("Blender failed to save the current file.")


def create_new_file(filepath: str, context: dict[str, Any]) -> None:
    """
    Create a brand-new .blend file under MONOS control.

    This resets Blender to factory settings with an empty scene, binds the provided
    MONOS context to that new scene, and saves immediately to 'filepath'.
    """
    _bpy = require_bpy()

    if not isinstance(filepath, str) or not filepath.strip():
        raise RuntimeError("create_new_file(filepath=..., context=...): filepath must be a non-empty string.")

    validated = _validate_context(context)

    try:
        result = _bpy.ops.wm.read_factory_settings(use_empty=True)
    except Exception as e:
        raise RuntimeError("Blender failed to reset to factory settings (empty scene).") from e
    if not isinstance(result, set) or "FINISHED" not in result:
        raise RuntimeError("Blender failed to reset to factory settings (empty scene).")

    _write_context_json(json.dumps(validated.as_dict(), ensure_ascii=False, separators=(",", ":")))

    try:
        result = _bpy.ops.wm.save_as_mainfile(filepath=filepath)
    except Exception as e:
        raise RuntimeError(f"Blender failed to save new file: {filepath!r}") from e
    if not isinstance(result, set) or "FINISHED" not in result:
        raise RuntimeError(f"Blender failed to save new file: {filepath!r}")


"""Pinned (starred) files in entity reference/concept folders."""

from __future__ import annotations

import json
from pathlib import Path

from monostudio.core.atomic_write import atomic_write_text
from monostudio.core.entity_folders import EntitySpecialFolderId

PINS_FILENAME = "ref_folder_pins.json"
PINS_SCHEMA = 1


def _pins_path(entity_root: Path) -> Path:
    return Path(entity_root) / ".monostudio" / PINS_FILENAME


def read_ref_folder_pins(entity_root: Path) -> dict[EntitySpecialFolderId, list[str]]:
    path = _pins_path(entity_root)
    out: dict[EntitySpecialFolderId, list[str]] = {"reference": [], "concept": []}
    if not path.is_file():
        return out
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return out
    if not isinstance(data, dict):
        return out
    for fid in ("reference", "concept"):
        raw = data.get(fid)
        if isinstance(raw, list):
            names: list[str] = []
            seen: set[str] = set()
            for item in raw:
                if isinstance(item, str):
                    name = item.strip()
                    key = name.casefold()
                    if name and key not in seen:
                        seen.add(key)
                        names.append(name)
            out[fid] = names
    return out


def pinned_file_names(entity_root: Path, folder_id: EntitySpecialFolderId) -> list[str]:
    return list(read_ref_folder_pins(entity_root).get(folder_id, []))


def is_ref_file_pinned(entity_root: Path, folder_id: EntitySpecialFolderId, filename: str) -> bool:
    key = (filename or "").casefold()
    if not key:
        return False
    return any(n.casefold() == key for n in pinned_file_names(entity_root, folder_id))


def toggle_ref_file_pin(
    entity_root: Path,
    folder_id: EntitySpecialFolderId,
    filename: str,
) -> bool:
    """Toggle pin for a top-level file name. Returns new pinned state."""
    name = (filename or "").strip()
    if not name:
        return False
    pins = read_ref_folder_pins(entity_root)
    current = list(pins.get(folder_id, []))
    key = name.casefold()
    pinned = any(n.casefold() == key for n in current)
    if pinned:
        current = [n for n in current if n.casefold() != key]
        new_state = False
    else:
        current.insert(0, name)
        new_state = True
    pins[folder_id] = current
    _write_pins(entity_root, pins)
    return new_state


def sort_files_with_pins(
    files: list[Path],
    pinned_names: list[str],
) -> list[Path]:
    """Starred files first (pin order), then the rest in original order."""
    if not pinned_names:
        return list(files)
    pin_order = {n.casefold(): i for i, n in enumerate(pinned_names)}
    pinned: list[Path] = []
    unpinned: list[Path] = []
    for path in files:
        if path.name.casefold() in pin_order:
            pinned.append(path)
        else:
            unpinned.append(path)
    pinned.sort(key=lambda p: pin_order.get(p.name.casefold(), 10_000))
    return pinned + unpinned


def _write_pins(entity_root: Path, pins: dict[EntitySpecialFolderId, list[str]]) -> None:
    mono = Path(entity_root) / ".monostudio"
    mono.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": PINS_SCHEMA,
        "reference": pins.get("reference", []),
        "concept": pins.get("concept", []),
    }
    content = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    atomic_write_text(_pins_path(entity_root), content, encoding="utf-8")

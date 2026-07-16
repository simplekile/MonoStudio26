"""Unified review sidecar — ranges + markers + draw in ``.monos/*.review.json``."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

REVIEW_SIDECAR_KIND = "review"
REVIEW_SIDECAR_VERSION = 1


def video_review_sidecar_path(video_path: Path) -> Path:
    """``<dir>/.monos/<video_name>.review.json`` next to the video."""
    return video_path.parent / ".monos" / f"{video_path.name}.review.json"


def sequence_review_sidecar_path(sequence_folder: Path) -> Path:
    """``<seq>/.monos/review.json`` inside the sequence folder."""
    return sequence_folder / ".monos" / "review.json"


def legacy_video_ranges_path(video_path: Path) -> Path:
    return Path(f"{video_path}.monos.ranges.json")


def legacy_video_markers_path(video_path: Path) -> Path:
    return Path(f"{video_path}.monos.markers.json")


def legacy_video_draw_path(video_path: Path) -> Path:
    return Path(f"{video_path}.monos.draw.json")


def legacy_sequence_ranges_path(sequence_folder: Path) -> Path:
    return sequence_folder / ".monos.seq_ranges.json"


def legacy_sequence_markers_path(sequence_folder: Path) -> Path:
    return sequence_folder / ".monos.markers.json"


def legacy_sequence_draw_path(sequence_folder: Path) -> Path:
    return sequence_folder / ".monos.draw.json"


def _source_meta(media_key: Path) -> dict[str, Any]:
    try:
        source_path = str(media_key.resolve())
    except OSError:
        source_path = str(media_key)
    return {
        "version": REVIEW_SIDECAR_VERSION,
        "kind": REVIEW_SIDECAR_KIND,
        "source": media_key.name,
        "source_path": source_path,
    }


def read_json_dict(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as e:
        logger.debug("load review json %s: %s", path, e)
        return None
    return data if isinstance(data, dict) else None


def write_json_dict(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _unlink_quiet(path: Path) -> None:
    try:
        if path.is_file():
            path.unlink()
    except OSError as e:
        logger.debug("remove sidecar %s: %s", path, e)


def _maybe_rmdir_monos(monos_dir: Path) -> None:
    try:
        if monos_dir.is_dir() and not any(monos_dir.iterdir()):
            monos_dir.rmdir()
    except OSError as e:
        logger.debug("rmdir %s: %s", monos_dir, e)


def _section_nonempty(data: dict[str, Any], key: str) -> bool:
    raw = data.get(key)
    if not isinstance(raw, list) or not raw:
        return False
    if key == "layers":
        return any(
            isinstance(layer, dict)
            and isinstance(layer.get("keyframes"), list)
            and bool(layer.get("keyframes"))
            for layer in raw
        )
    return True


def review_payload_is_empty(data: dict[str, Any]) -> bool:
    return not (
        _section_nonempty(data, "ranges")
        or _section_nonempty(data, "markers")
        or _section_nonempty(data, "layers")
    )


def _merge_legacy_list_section(
    data: dict[str, Any],
    key: str,
    legacy_path: Path,
) -> None:
    legacy_data = read_json_dict(legacy_path)
    if legacy_data is None:
        return
    raw = legacy_data.get(key)
    if isinstance(raw, list):
        data[key] = raw


def load_video_review_dict(video_path: Path) -> dict[str, Any]:
    """Load unified review dict, seeding from legacy sidecars when needed."""
    path = video_review_sidecar_path(video_path)
    existing = read_json_dict(path)
    if existing is not None:
        data = dict(existing)
        data.setdefault("kind", REVIEW_SIDECAR_KIND)
        data["version"] = REVIEW_SIDECAR_VERSION
        data.update(_source_meta(video_path))
        return data

    data = _source_meta(video_path)
    _merge_legacy_list_section(data, "ranges", legacy_video_ranges_path(video_path))
    _merge_legacy_list_section(data, "markers", legacy_video_markers_path(video_path))
    _merge_legacy_list_section(data, "layers", legacy_video_draw_path(video_path))
    return data


def load_sequence_review_dict(sequence_folder: Path) -> dict[str, Any]:
    path = sequence_review_sidecar_path(sequence_folder)
    existing = read_json_dict(path)
    if existing is not None:
        data = dict(existing)
        data.setdefault("kind", REVIEW_SIDECAR_KIND)
        data["version"] = REVIEW_SIDECAR_VERSION
        data.update(_source_meta(sequence_folder))
        return data

    data = _source_meta(sequence_folder)
    _merge_legacy_list_section(data, "ranges", legacy_sequence_ranges_path(sequence_folder))
    _merge_legacy_list_section(data, "markers", legacy_sequence_markers_path(sequence_folder))
    _merge_legacy_list_section(data, "layers", legacy_sequence_draw_path(sequence_folder))
    return data


def write_video_review_dict(
    video_path: Path,
    data: dict[str, Any],
    *,
    unlink_legacy: frozenset[str] | None = None,
) -> None:
    path = video_review_sidecar_path(video_path)
    payload = dict(data)
    payload.update(_source_meta(video_path))
    payload["kind"] = REVIEW_SIDECAR_KIND
    payload["version"] = REVIEW_SIDECAR_VERSION
    if review_payload_is_empty(payload):
        _unlink_quiet(path)
        _maybe_rmdir_monos(path.parent)
    else:
        write_json_dict(path, payload)
    sections = unlink_legacy or frozenset()
    if "ranges" in sections:
        _unlink_quiet(legacy_video_ranges_path(video_path))
    if "markers" in sections:
        _unlink_quiet(legacy_video_markers_path(video_path))
    if "layers" in sections:
        _unlink_quiet(legacy_video_draw_path(video_path))


def write_sequence_review_dict(
    sequence_folder: Path,
    data: dict[str, Any],
    *,
    unlink_legacy: frozenset[str] | None = None,
) -> None:
    path = sequence_review_sidecar_path(sequence_folder)
    payload = dict(data)
    payload.update(_source_meta(sequence_folder))
    payload["kind"] = REVIEW_SIDECAR_KIND
    payload["version"] = REVIEW_SIDECAR_VERSION
    if review_payload_is_empty(payload):
        _unlink_quiet(path)
        _maybe_rmdir_monos(path.parent)
    else:
        write_json_dict(path, payload)
    sections = unlink_legacy or frozenset()
    if "ranges" in sections:
        _unlink_quiet(legacy_sequence_ranges_path(sequence_folder))
    if "markers" in sections:
        _unlink_quiet(legacy_sequence_markers_path(sequence_folder))
    if "layers" in sections:
        _unlink_quiet(legacy_sequence_draw_path(sequence_folder))

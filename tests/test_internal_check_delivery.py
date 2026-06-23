"""Internal check / Delivery helpers and core send workflow."""
from __future__ import annotations

from pathlib import Path

import pytest

from monostudio.core.delivery_reader import read_delivery_meta
from monostudio.core.internal_check_reader import (
    read_internal_check_meta,
    send_internal_check_to_delivery,
)
from monostudio.core.outbox_reader import ensure_outbox_source_folders
from monostudio.core.internal_check_reader import ensure_internal_check_root, get_internal_check_root
from monostudio.ui_qt.inbox_split_view import inbox_tree_selection_hint_text
from monostudio.ui_qt.outbox_page_widget import _normalize_source_type


def test_normalize_source_type_defaults_to_client() -> None:
    assert _normalize_source_type("") == "client"
    assert _normalize_source_type("freelancer") == "freelancer"


def test_selection_hint_text_inbox_mentions_distribute() -> None:
    text = inbox_tree_selection_hint_text("inbox", 2)
    assert text is not None
    assert "Distribute" in text
    assert "2 items" in text


def test_selection_hint_text_preview_only_no_distribute() -> None:
    for mode in ("internal_check", "delivery"):
        text = inbox_tree_selection_hint_text(mode, 3)
        assert text == "3 items selected"
        assert "Distribute" not in (text or "")


def test_selection_hint_text_zero_count_hidden() -> None:
    assert inbox_tree_selection_hint_text("delivery", 0) is None


def test_send_internal_check_to_delivery_moves_file_and_updates_meta(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    ensure_outbox_source_folders(project_root)
    ensure_internal_check_root(project_root)

    date_folder = "260623_Test"
    ic_root = get_internal_check_root(project_root)
    src_dir = ic_root / date_folder
    src_dir.mkdir(parents=True)
    src_file = src_dir / "deliverable.mov"
    src_file.write_bytes(b"fake-video")

    ic_meta = read_internal_check_meta(project_root)
    ic_meta[f"{date_folder}/deliverable.mov"] = {
        "added_at": "2026-06-23T10:00:00Z",
        "description": "internal note",
    }
    from monostudio.core.internal_check_reader import write_internal_check_meta

    write_internal_check_meta(project_root, ic_meta)

    dest = send_internal_check_to_delivery(
        project_root,
        src_file,
        "client",
        date_folder,
        None,
    )
    assert dest is not None
    assert dest.is_file()
    assert not src_file.exists()

    ic_after = read_internal_check_meta(project_root)
    assert f"{date_folder}/deliverable.mov" not in ic_after

    dl_meta = read_delivery_meta(project_root)
    rel = f"client/{date_folder}/deliverable.mov"
    assert rel in dl_meta
    assert dl_meta[rel].get("source") == "client"
    assert dl_meta[rel].get("description") == "internal note"


def test_send_internal_check_to_delivery_rejects_outside_root(tmp_path: Path) -> None:
    project_root = tmp_path / "proj"
    project_root.mkdir()
    ensure_internal_check_root(project_root)
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    assert send_internal_check_to_delivery(project_root, outside, "client", "260623_X", None) is None

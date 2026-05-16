"""Tests for entity reference/concept folder watcher helpers."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monostudio.ui_qt.fs_watcher import (
    _entity_path_if_special_folder_touch,
    append_entity_special_folder_watch_paths,
)


class TestEntitySpecialFolderWatch(unittest.TestCase):
    def test_append_watch_paths_includes_reference_and_files(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            entity = Path(td) / "assets" / "char" / "hero"
            ref = entity / "reference"
            ref.mkdir(parents=True)
            img = ref / "ref.png"
            img.write_bytes(b"\x89PNG\r\n")
            concept = entity / "concept"
            concept.mkdir()
            to_add: list[str] = []
            seen: set[str] = set()
            append_entity_special_folder_watch_paths(entity, to_add, seen, max_paths=200)
            joined = " ".join(to_add)
            self.assertIn("reference", joined)
            self.assertIn("concept", joined)
            self.assertIn("ref.png", joined)

    def test_classify_special_folder_touch_under_reference(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            assets = root / "assets" / "char" / "hero"
            ref_file = assets / "reference" / "img.jpg"
            ref_file.parent.mkdir(parents=True)
            ref_file.write_bytes(b"")
            try:
                from monostudio.core.type_registry import TypeRegistry

                reg = TypeRegistry.for_project(root)
                reg.ensure_type("character", folder="char")
            except Exception:
                self.skipTest("TypeRegistry setup unavailable")
            ent = _entity_path_if_special_folder_touch(
                root,
                ref_file,
                TypeRegistry.for_project(root),
                "assets",
                "shots",
            )
            self.assertIsNotNone(ent)
            self.assertTrue(str(ent).endswith("hero") or "hero" in str(ent))


if __name__ == "__main__":
    unittest.main()

"""Tests for pipeline list layout and selection store."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from monostudio.ui_qt.pipeline_list_layout import ListSlot, PipelineListLayout
from monostudio.ui_qt.pipeline_selection import PipelineSelectionStore


class TestPipelineListLayout(unittest.TestCase):
    def test_asset_shot_slots(self) -> None:
        layout = PipelineListLayout.for_context("asset")
        self.assertIn(ListSlot.THUMB, layout.visible_slots())
        self.assertIn(ListSlot.STATUS, layout.visible_slots())
        self.assertNotIn(ListSlot.PATH, layout.visible_slots())

    def test_project_hides_path(self) -> None:
        layout = PipelineListLayout.for_context("project")
        self.assertIn(ListSlot.PATH, layout.slots())
        self.assertNotIn(ListSlot.PATH, layout.visible_slots())

    def test_slot_rect_positions(self) -> None:
        layout = PipelineListLayout.for_context("asset")
        row = __import__("PySide6.QtCore", fromlist=["QRect"]).QRect(10, 20, layout.total_width(), 56)
        name_rect = layout.slot_rect(row, ListSlot.NAME)
        self.assertGreater(name_rect.left(), row.left())
        self.assertEqual(name_rect.height(), 56)

    def test_set_status_width(self) -> None:
        layout = PipelineListLayout.for_context("shot")
        layout.set_status_width(160)
        self.assertGreaterEqual(layout.widths[ListSlot.STATUS], 160)

    def test_slot_at_content_x(self) -> None:
        layout = PipelineListLayout.for_context("asset")
        self.assertEqual(layout.slot_at_content_x(0), ListSlot.INDEX)
        past_index = layout.widths[ListSlot.INDEX]
        self.assertEqual(layout.slot_at_content_x(past_index), ListSlot.THUMB)

    def test_sticky_width(self) -> None:
        layout = PipelineListLayout.for_context("asset")
        expected = layout.widths[ListSlot.INDEX] + layout.widths[ListSlot.THUMB] + layout.widths[ListSlot.NAME]
        self.assertEqual(layout.sticky_width(), expected)
        self.assertIn(ListSlot.NOTES, layout.scrollable_slots())

    def test_content_x_sticky_zone(self) -> None:
        layout = PipelineListLayout.for_context("asset")
        sticky_w = layout.sticky_width()
        self.assertEqual(layout.content_x_for_viewport_pos(20, -100, scroll_x=100), 20)
        self.assertEqual(layout.content_x_for_viewport_pos(sticky_w + 50, -100, scroll_x=100), 150)

    def test_list_dcc_badge_rects(self) -> None:
        from PySide6.QtCore import QRect

        from monostudio.ui_qt.pipeline_row_paint import list_dcc_badge_rects

        cell = QRect(0, 0, 200, 56)
        rects = list_dcc_badge_rects(cell, [("maya", "exists"), ("blender", "exists")])
        self.assertEqual(len(rects), 2)
        self.assertEqual(rects[0][1], "maya")
        self.assertTrue(rects[0][0].left() >= cell.left())


class TestPipelineSelectionStore(unittest.TestCase):
    def test_single_select(self) -> None:
        store = PipelineSelectionStore()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hero"
            store.set_single(p)
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.current(), p)

    def test_toggle_multi(self) -> None:
        store = PipelineSelectionStore()
        with tempfile.TemporaryDirectory() as td:
            a = Path(td) / "a"
            b = Path(td) / "b"
            store.set_single(a)
            store.toggle(b)
            self.assertEqual(store.count(), 2)
            store.toggle(a)
            self.assertEqual(store.count(), 1)
            self.assertEqual(store.current(), b)

    def test_select_many(self) -> None:
        store = PipelineSelectionStore()
        with tempfile.TemporaryDirectory() as td:
            paths = [Path(td) / "a", Path(td) / "b", Path(td) / "c"]
            store.select_many(paths, current=paths[1])
            self.assertEqual(store.count(), 3)
            self.assertEqual(store.current(), paths[1])


if __name__ == "__main__":
    unittest.main()

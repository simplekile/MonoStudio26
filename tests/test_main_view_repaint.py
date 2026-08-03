"""Tests for granular main view repaint helpers (Phase 0b)."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from PySide6.QtWidgets import QApplication

from monostudio.ui_qt.main_view import MainView


class TestMainViewGranularRepaint(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._app = QApplication.instance() or QApplication([])

    def test_repaint_rows_for_paths_emits_per_row(self) -> None:
        view = MainView()
        view._tile_row_count = MagicMock(return_value=3)  # type: ignore[method-assign]
        view._row_for_item_id = MagicMock(side_effect=lambda pid: {"p0": 0, "p2": 2}.get(pid))  # type: ignore[method-assign]
        view.interaction_fast_paint = MagicMock(return_value=False)  # type: ignore[method-assign]
        view._tile_model = MagicMock()
        view._list_model = MagicMock()
        view._tile_view = MagicMock()
        view._list_view = MagicMock()

        view.repaint_rows_for_paths(["p0", "p2", "missing"])

        self.assertEqual(view._tile_model.dataChanged.emit.call_count, 2)
        self.assertEqual(view._list_model.refresh_row.call_count, 2)
        view._list_model.refresh_row.assert_any_call(0)
        view._list_model.refresh_row.assert_any_call(2)

    def test_repaint_rows_skipped_during_fast_paint(self) -> None:
        view = MainView()
        view.interaction_fast_paint = MagicMock(return_value=True)  # type: ignore[method-assign]
        view._deferred_full_repaint_pending = False

        view.repaint_rows_for_paths(["p0"])

        self.assertTrue(view._deferred_full_repaint_pending)


if __name__ == "__main__":
    unittest.main()

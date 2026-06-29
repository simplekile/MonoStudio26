"""Draw tool panel for unified review player."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QSpinBox,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.review_draw import ReviewDrawLayer
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MONOS_COLORS
from monostudio.ui_qt.video_review_draw_stack import VideoReviewDrawStackWidget

_TOOLBAR_ROW_H = 28  # align with range/marker sort row in ReviewToolsPanel


def _icon_tool_btn(parent: QWidget, icon: str, tip: str, *, checkable: bool = True) -> QToolButton:
    btn = QToolButton(parent)
    btn.setObjectName("VideoReviewDrawTool")
    btn.setToolTip(tip)
    btn.setIcon(lucide_icon(icon, size=16, color_hex=MONOS_COLORS["text_label"]))
    btn.setIconSize(QSize(16, 16))
    btn.setFixedSize(32, 32)
    btn.setCheckable(checkable)
    btn.setAutoRaise(True)
    return btn


class VideoReviewDrawTransportActions(QWidget):
    """Minimal draw actions in the transport bar (structure on sidebar / brush on video)."""

    keyframe_add_requested = Signal()
    layer_add_requested = Signal()
    undo_stroke_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawTransportActions")
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)

        self._btn_add_key = _icon_tool_btn(
            self, "diamond", "Add keyframe at playhead", checkable=False
        )
        self._btn_add_key.clicked.connect(self.keyframe_add_requested.emit)
        lay.addWidget(self._btn_add_key)

        self._btn_add_layer = _icon_tool_btn(self, "layers", "Add layer", checkable=False)
        self._btn_add_layer.clicked.connect(self.layer_add_requested.emit)
        lay.addWidget(self._btn_add_layer)

        self._btn_undo = _icon_tool_btn(self, "undo-2", "Undo stroke (Ctrl+Z)", checkable=False)
        self._btn_undo.clicked.connect(self.undo_stroke_requested.emit)
        lay.addWidget(self._btn_undo)


class VideoReviewDrawPanel(QWidget):
    keyframe_selected = Signal(str, int)
    layer_selected = Signal(str)
    keyframe_add_requested = Signal()
    layer_add_requested = Signal()
    undo_stroke_requested = Signal()
    keyframe_edit_frame_changed = Signal(int)
    keyframe_hold_changed = Signal(int)
    keyframe_delete_requested = Signal()
    layer_visibility_toggle_requested = Signal(str)
    keyframe_visibility_toggle_requested = Signal(str, int)
    layer_default_hold_changed = Signal(str, int)
    layer_delete_requested = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawPanel")
        self._active_keyframe_frame: int | None = None
        self._active_layer_id: str | None = None
        self._edit_sync_guard = False
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)

        self._toolbar_row = QWidget(self)
        self._toolbar_row.setObjectName("VideoReviewDrawToolbarRow")
        self._toolbar_row.setFixedHeight(_TOOLBAR_ROW_H)
        edit_lay = QHBoxLayout(self._toolbar_row)
        edit_lay.setContentsMargins(0, 0, 0, 0)
        edit_lay.setSpacing(8)
        self._edit_key_label = QLabel("Edit key", self._toolbar_row)
        edit_lay.addWidget(self._edit_key_label)
        self._edit_frame_spin = QSpinBox(self._toolbar_row)
        self._edit_frame_spin.setPrefix("F")
        self._edit_frame_spin.setToolTip("Keyframe frame (or drag diamond on timeline)")
        self._edit_frame_spin.valueChanged.connect(self._on_edit_frame_changed)
        edit_lay.addWidget(self._edit_frame_spin)
        self._btn_delete_key = _icon_tool_btn(self._toolbar_row, "trash-2", "Delete keyframe (Del)")
        self._btn_delete_key.setCheckable(False)
        self._btn_delete_key.clicked.connect(self.keyframe_delete_requested.emit)
        edit_lay.addWidget(self._btn_delete_key)
        edit_lay.addStretch(1)
        lay.addWidget(self._toolbar_row)
        self._set_keyframe_toolbar_visible(False)

        self._stack = VideoReviewDrawStackWidget(self)
        self._stack.layer_selected.connect(self.layer_selected.emit)
        self._stack.keyframe_selected.connect(self.keyframe_selected.emit)
        self._stack.layer_visibility_toggled.connect(self.layer_visibility_toggle_requested.emit)
        self._stack.keyframe_visibility_toggled.connect(self.keyframe_visibility_toggle_requested.emit)
        self._stack.layer_default_hold_changed.connect(self.layer_default_hold_changed.emit)
        self._stack.layer_delete_requested.connect(self.layer_delete_requested.emit)
        self._stack.keyframe_hold_changed.connect(self.keyframe_hold_changed.emit)
        lay.addWidget(self._stack, 1)

        footer = QWidget(self)
        footer.setObjectName("VideoPreviewListPanelFooter")
        footer.setFixedHeight(64)
        footer_lay = QVBoxLayout(footer)
        footer_lay.setContentsMargins(0, 0, 0, 0)
        footer_lay.setSpacing(0)
        hint = QLabel("Brush on video · D exit · E edit key on scrubber", footer)
        hint.setObjectName("DialogHint")
        hint.setWordWrap(True)
        footer_lay.addWidget(hint, 1, Qt.AlignmentFlag.AlignTop)
        lay.addWidget(footer, 0)

    def _set_keyframe_toolbar_visible(self, visible: bool) -> None:
        for w in (self._edit_key_label, self._edit_frame_spin, self._btn_delete_key):
            w.setVisible(visible)

    def set_keyframe_edit_mode(
        self,
        enabled: bool,
        *,
        frame: int | None = None,
        hold: int = 1,
        max_frame: int = 0,
        layer_id: str | None = None,
    ) -> None:
        self._set_keyframe_toolbar_visible(bool(enabled))
        self._stack.set_keyframe_edit_state(
            enabled=bool(enabled),
            layer_id=layer_id,
            frame=frame,
            hold=hold,
        )
        if not enabled:
            return
        self._edit_sync_guard = True
        try:
            self._edit_frame_spin.setRange(0, max(0, int(max_frame)))
            if frame is not None:
                self._edit_frame_spin.setValue(int(frame))
        finally:
            self._edit_sync_guard = False

    def sync_keyframe_edit_hold(self, hold: int) -> None:
        self._stack.sync_keyframe_edit_hold(hold)

    def _on_edit_frame_changed(self, value: int) -> None:
        if self._edit_sync_guard:
            return
        self.keyframe_edit_frame_changed.emit(int(value))

    def set_active_keyframe(self, frame: int | None) -> None:
        self._active_keyframe_frame = frame

    def set_active_layer_id(self, layer_id: str | None) -> None:
        self._active_layer_id = layer_id

    def set_layers(
        self,
        layers: list[ReviewDrawLayer],
        *,
        active_frame: int | None,
        active_layer_id: str | None,
    ) -> None:
        self._active_keyframe_frame = active_frame
        self._active_layer_id = active_layer_id
        self._stack.set_layers(
            layers,
            active_frame=active_frame,
            active_layer_id=active_layer_id,
        )

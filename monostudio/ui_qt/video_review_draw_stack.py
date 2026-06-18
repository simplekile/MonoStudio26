"""Affinity-style layer / keyframe stack for review draw panel."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QSpinBox,
    QToolButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.review_draw import (
    ReviewDrawLayer,
    default_hold_frames_for_layer,
    hold_frames_for_keyframe,
    keyframe_hold_end,
)
from monostudio.core.video_media import format_frame_label
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import position_popup_near_anchor
from monostudio.ui_qt.style import MONOS_COLORS, monos_font

_ROW_LAYER_H = 40
_ROW_KEY_H = 34
_EYE_COL_W = 28
_DELETE_COL_W = 28
_HOLD_COL_W = 44
_ICON_COL_W = 20
_DISCLOSURE_COL_W = 16
_KF_ICON_COLOR = "#f97316"
_MUTED_COLOR = MONOS_COLORS.get("text_muted", "#71717a")
_LAYER_ICON_COLOR = MONOS_COLORS["text_label"]
_LAYER_ICON_ACTIVE_COLOR = MONOS_COLORS.get("blue_400", "#60a5fa")


class _DrawStackDisclosureButton(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawStackDisclosure")
        self.setIconSize(QSize(14, 14))
        self.setFixedSize(_DISCLOSURE_COL_W, _DISCLOSURE_COL_W)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_disclosure_state(self, *, expanded: bool, enabled: bool = True) -> None:
        muted = MONOS_COLORS.get("text_muted", "#71717a")
        if not enabled:
            self.setEnabled(False)
            self.setIcon(lucide_icon("chevron-right", size=14, color_hex=muted))
            self.setToolTip("")
            return
        self.setEnabled(True)
        icon = "chevron-down" if expanded else "chevron-right"
        color = MONOS_COLORS["text_label"]
        self.setToolTip("Hide keyframes" if expanded else "Show keyframes")
        self.setIcon(lucide_icon(icon, size=14, color_hex=color))


class _DrawStackTree(QTreeWidget):
    """Tree without native branch hit-target; expand only via disclosure column."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setExpandsOnDoubleClick(False)
        self.setItemsExpandable(False)


class _DrawStackEyeButton(QToolButton):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawStackEye")
        self.setIconSize(QSize(14, 14))
        self.setFixedSize(_EYE_COL_W, _EYE_COL_W)
        self.setAutoRaise(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def set_visible_state(self, visible: bool, *, kind: str = "layer") -> None:
        icon = "eye" if visible else "eye-off"
        color = MONOS_COLORS["text_label"] if visible else MONOS_COLORS.get("text_muted", "#71717a")
        label = "keyframe" if kind == "keyframe" else "layer"
        self.setToolTip(f"Hide {label}" if visible else f"Show {label}")
        self.setIcon(lucide_icon(icon, size=14, color_hex=color))


class _DrawKeyframeHoldPopup(QFrame):
    hold_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.setObjectName("VideoReviewDrawKeyframeHoldPopup")
        self._sync_guard = False
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)
        label = QLabel("Hold", self)
        label.setObjectName("VideoReviewDrawStackRowTitle")
        label.setFont(monos_font("Inter", 12, QFont.Weight.DemiBold))
        lay.addWidget(label)
        self._spin = QSpinBox(self)
        self._spin.setObjectName("VideoReviewDrawStackHoldSpin")
        self._spin.setRange(1, 9999)
        self._spin.setToolTip("Hold duration in frames ([ ] while editing)")
        self._spin.valueChanged.connect(self._on_value_changed)
        lay.addWidget(self._spin)

    def set_hold(self, hold: int) -> None:
        self._sync_guard = True
        try:
            self._spin.setValue(max(1, int(hold)))
        finally:
            self._sync_guard = False

    def _on_value_changed(self, value: int) -> None:
        if self._sync_guard:
            return
        self.hold_changed.emit(int(value))


class _DrawStackKeyframeRowWidget(QWidget):
    visibility_toggled = Signal()

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        visible: bool,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawStackRow")
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 2)
        root.setSpacing(8)

        disclosure_spacer = QWidget(self)
        disclosure_spacer.setFixedSize(_DISCLOSURE_COL_W, _DISCLOSURE_COL_W)
        root.addWidget(disclosure_spacer, 0, Qt.AlignmentFlag.AlignVCenter)

        icon_lab = QLabel(self)
        icon_lab.setFixedSize(_ICON_COL_W, _ICON_COL_W)
        icon_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(icon_lab, 0, Qt.AlignmentFlag.AlignVCenter)
        self._icon_lab = icon_lab

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        name = QLabel(title, self)
        name.setObjectName("VideoReviewDrawStackRowTitle")
        name.setFont(monos_font("Inter", 12, QFont.Weight.DemiBold))
        text_col.addWidget(name)
        if subtitle:
            detail = QLabel(subtitle, self)
            detail.setObjectName("VideoReviewDrawStackRowDetail")
            detail.setFont(monos_font("JetBrains Mono", 10, QFont.Weight.Medium))
            text_col.addWidget(detail)
        root.addLayout(text_col, 1)

        spacer = QWidget(self)
        spacer.setFixedSize(_DELETE_COL_W, _DELETE_COL_W)
        root.addWidget(spacer, 0, Qt.AlignmentFlag.AlignVCenter)

        self._eye = _DrawStackEyeButton(self)
        self._eye.clicked.connect(self.visibility_toggled.emit)
        self.apply_visibility_display(key_visible=visible, layer_visible=True)
        root.addWidget(self._eye, 0, Qt.AlignmentFlag.AlignVCenter)

    def apply_visibility_display(self, *, key_visible: bool, layer_visible: bool) -> None:
        self._eye.set_visible_state(key_visible, kind="keyframe")
        muted = not key_visible or not layer_visible
        title = self.findChild(QLabel, "VideoReviewDrawStackRowTitle")
        detail = self.findChild(QLabel, "VideoReviewDrawStackRowDetail")
        if title is not None:
            title.setStyleSheet(f"color: {_MUTED_COLOR};" if muted else "")
        if detail is not None:
            detail.setStyleSheet(f"color: {_MUTED_COLOR};" if muted else "")
        icon_color = _MUTED_COLOR if muted else _KF_ICON_COLOR
        self._icon_lab.setPixmap(lucide_icon("diamond", size=14, color_hex=icon_color).pixmap(14, 14))

    def set_visible_state(self, visible: bool) -> None:
        self.apply_visibility_display(key_visible=visible, layer_visible=True)


class _DrawStackLayerRowWidget(QWidget):
    visibility_toggled = Signal()
    default_hold_changed = Signal(int)
    delete_requested = Signal()
    expand_toggled = Signal()

    def __init__(
        self,
        *,
        title: str,
        subtitle: str,
        visible: bool,
        default_hold: int,
        expanded: bool = False,
        has_keyframes: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawStackRow")
        self._hold_sync_guard = False
        self._visible = bool(visible)
        self._active = False
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 2, 4, 2)
        root.setSpacing(8)

        self._disclosure = _DrawStackDisclosureButton(self)
        self._disclosure.clicked.connect(self.expand_toggled.emit)
        self.set_disclosure_state(expanded=expanded, has_keyframes=has_keyframes)
        root.addWidget(self._disclosure, 0, Qt.AlignmentFlag.AlignVCenter)

        icon_lab = QLabel(self)
        icon_lab.setFixedSize(_ICON_COL_W, _ICON_COL_W)
        icon_lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(icon_lab, 0, Qt.AlignmentFlag.AlignVCenter)
        self._icon_lab = icon_lab

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(1)
        name = QLabel(title, self)
        name.setObjectName("VideoReviewDrawStackRowTitle")
        name.setFont(monos_font("Inter", 12, QFont.Weight.DemiBold))
        text_col.addWidget(name)
        if subtitle:
            detail = QLabel(subtitle, self)
            detail.setObjectName("VideoReviewDrawStackRowDetail")
            detail.setFont(monos_font("JetBrains Mono", 10, QFont.Weight.Medium))
            text_col.addWidget(detail)
        root.addLayout(text_col, 1)

        self._hold_spin = QSpinBox(self)
        self._hold_spin.setObjectName("VideoReviewDrawStackHoldSpin")
        self._hold_spin.setRange(1, 9999)
        self._hold_spin.setFixedWidth(_HOLD_COL_W)
        self._hold_spin.setToolTip("Default hold for new keyframes on this layer")
        self._hold_spin.valueChanged.connect(self._on_hold_changed)
        self.set_default_hold(default_hold)
        root.addWidget(self._hold_spin, 0, Qt.AlignmentFlag.AlignVCenter)

        self._delete = QToolButton(self)
        self._delete.setObjectName("VideoReviewDrawStackDelete")
        self._delete.setIconSize(QSize(14, 14))
        self._delete.setFixedSize(_DELETE_COL_W, _DELETE_COL_W)
        self._delete.setAutoRaise(True)
        self._delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self._delete.setToolTip("Delete layer")
        self._delete.setIcon(
            lucide_icon("trash-2", size=14, color_hex=MONOS_COLORS.get("text_muted", "#71717a"))
        )
        self._delete.clicked.connect(self.delete_requested.emit)
        root.addWidget(self._delete, 0, Qt.AlignmentFlag.AlignVCenter)

        self._eye = _DrawStackEyeButton(self)
        self._eye.clicked.connect(self.visibility_toggled.emit)
        self.set_visible_state(visible)
        root.addWidget(self._eye, 0, Qt.AlignmentFlag.AlignVCenter)

    def set_delete_enabled(self, enabled: bool) -> None:
        self._delete.setEnabled(bool(enabled))

    def set_disclosure_state(self, *, expanded: bool, has_keyframes: bool) -> None:
        self._disclosure.set_disclosure_state(expanded=expanded, enabled=has_keyframes)

    def set_default_hold(self, hold: int) -> None:
        self._hold_sync_guard = True
        try:
            self._hold_spin.setValue(max(1, int(hold)))
        finally:
            self._hold_sync_guard = False

    def _on_hold_changed(self, value: int) -> None:
        if self._hold_sync_guard:
            return
        self.default_hold_changed.emit(int(value))

    def set_active_state(self, active: bool) -> None:
        self._active = bool(active)
        self._refresh_layer_icon()

    def _refresh_layer_icon(self) -> None:
        if self._active:
            icon_color = _LAYER_ICON_ACTIVE_COLOR
        elif not self._visible:
            icon_color = _MUTED_COLOR
        else:
            icon_color = _LAYER_ICON_COLOR
        self._icon_lab.setPixmap(lucide_icon("layers", size=14, color_hex=icon_color).pixmap(14, 14))

    def set_visible_state(self, visible: bool) -> None:
        self._visible = bool(visible)
        self._eye.set_visible_state(visible, kind="layer")
        title = self.findChild(QLabel, "VideoReviewDrawStackRowTitle")
        detail = self.findChild(QLabel, "VideoReviewDrawStackRowDetail")
        if title is not None:
            title.setStyleSheet(f"color: {_MUTED_COLOR};" if not visible else "")
        if detail is not None:
            detail.setStyleSheet(f"color: {_MUTED_COLOR};" if not visible else "")
        self._refresh_layer_icon()


class _DrawStackTableHeader(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawStackHeader")
        root = QHBoxLayout(self)
        root.setContentsMargins(4, 0, 4, 0)
        root.setSpacing(8)
        muted = MONOS_COLORS.get("text_muted", "#71717a")
        disclosure = _stack_header_icon(
            self,
            "chevron-down",
            "Expand layer to show keyframes",
            color_hex=muted,
            width=_DISCLOSURE_COL_W,
        )
        root.addWidget(disclosure, 0)
        icon_spacer = QWidget(self)
        icon_spacer.setFixedWidth(_ICON_COL_W)
        root.addWidget(icon_spacer, 0)
        name = _stack_header_icon(
            self,
            "tag",
            "Name — layer and keyframe",
            color_hex=muted,
        )
        root.addWidget(name, 1)
        hold = _stack_header_icon(
            self,
            "timer",
            "New hold — default hold for new keyframes on this layer",
            color_hex=muted,
            width=_HOLD_COL_W,
        )
        root.addWidget(hold, 0)
        vis = _stack_header_icon(
            self,
            "eye",
            "Show — layer and keyframe visibility",
            color_hex=muted,
            width=_EYE_COL_W,
        )
        root.addWidget(vis, 0)


def _stack_header_icon(
    parent: QWidget,
    icon: str,
    tip: str,
    *,
    color_hex: str,
    width: int | None = None,
) -> QLabel:
    lab = QLabel(parent)
    lab.setObjectName("VideoReviewDrawStackHeaderIcon")
    lab.setPixmap(lucide_icon(icon, size=12, color_hex=color_hex).pixmap(12, 12))
    lab.setToolTip(tip)
    lab.setAlignment(Qt.AlignmentFlag.AlignCenter)
    if width is not None:
        lab.setFixedWidth(width)
    return lab


class VideoReviewDrawStackWidget(QWidget):
    layer_selected = Signal(str)
    keyframe_selected = Signal(str, int)
    layer_visibility_toggled = Signal(str)
    keyframe_visibility_toggled = Signal(str, int)
    layer_default_hold_changed = Signal(str, int)
    layer_delete_requested = Signal(str)
    keyframe_hold_changed = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoReviewDrawStackWidget")
        self._active_layer_id: str | None = None
        self._active_keyframe_frame: int | None = None
        self._expanded_layer_ids: set[str] = set()
        self._selection_guard = False
        self._edit_popup_enabled = False
        self._edit_layer_id: str | None = None
        self._edit_frame: int | None = None
        self._edit_hold = 1

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addWidget(_DrawStackTableHeader(self))

        self._tree = _DrawStackTree(self)
        self._tree.setObjectName("VideoReviewDrawStack")
        self._tree.setHeaderHidden(True)
        self._tree.setRootIsDecorated(False)
        self._tree.setIndentation(0)
        self._tree.setAnimated(False)
        self._tree.setUniformRowHeights(False)
        self._tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._tree.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._tree.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._tree.currentItemChanged.connect(self._on_current_item_changed)
        self._tree.itemExpanded.connect(self._on_layer_item_expansion_changed)
        self._tree.itemCollapsed.connect(self._on_layer_item_expansion_changed)
        lay.addWidget(self._tree, 1)

        self._hold_popup = _DrawKeyframeHoldPopup(self)
        self._hold_popup.hold_changed.connect(self.keyframe_hold_changed.emit)

    def set_keyframe_edit_state(
        self,
        *,
        enabled: bool,
        layer_id: str | None = None,
        frame: int | None = None,
        hold: int = 1,
    ) -> None:
        self._edit_popup_enabled = bool(enabled)
        self._edit_layer_id = layer_id
        self._edit_frame = frame
        self._edit_hold = max(1, int(hold))
        if self._edit_popup_enabled:
            self._hold_popup.set_hold(self._edit_hold)
            self._position_hold_popup()
        else:
            self._hold_popup.hide()

    def sync_keyframe_edit_hold(self, hold: int) -> None:
        self._edit_hold = max(1, int(hold))
        if self._edit_popup_enabled:
            self._hold_popup.set_hold(self._edit_hold)

    def set_layers(
        self,
        layers: list[ReviewDrawLayer],
        *,
        active_frame: int | None,
        active_layer_id: str | None,
    ) -> None:
        self._active_keyframe_frame = active_frame
        self._active_layer_id = active_layer_id
        valid_layer_ids = {layer.id for layer in layers}
        self._expanded_layer_ids &= valid_layer_ids
        self._selection_guard = True
        self._tree.blockSignals(True)
        try:
            self._tree.clear()
            select_item: QTreeWidgetItem | None = None
            for layer in layers:
                kf_count = len(layer.keyframes)
                stroke_count = sum(len(kf.strokes) for kf in layer.keyframes)
                layer_item = QTreeWidgetItem()
                layer_item.setData(0, Qt.ItemDataRole.UserRole, ("layer", layer.id))
                layer_item.setSizeHint(0, QSize(0, _ROW_LAYER_H))
                self._tree.addTopLevelItem(layer_item)
                layer_expanded = layer.id in self._expanded_layer_ids
                layer_row = _DrawStackLayerRowWidget(
                    title=layer.name or "Layer",
                    subtitle=f"{kf_count} key · {stroke_count} stroke",
                    visible=layer.visible,
                    default_hold=default_hold_frames_for_layer(layer),
                    expanded=layer_expanded,
                    has_keyframes=kf_count > 0,
                )
                layer_row.visibility_toggled.connect(
                    lambda lid=layer.id: self.layer_visibility_toggled.emit(lid)
                )
                layer_row.default_hold_changed.connect(
                    lambda hold, lid=layer.id: self.layer_default_hold_changed.emit(lid, hold)
                )
                layer_row.delete_requested.connect(
                    lambda lid=layer.id: self.layer_delete_requested.emit(lid)
                )
                layer_row.expand_toggled.connect(
                    lambda item=layer_item: self._toggle_layer_expanded(item)
                )
                layer_row.set_delete_enabled(True)
                layer_row.set_active_state(
                    active_layer_id is not None and layer.id == active_layer_id
                )
                self._tree.setItemWidget(layer_item, 0, layer_row)

                for kf in sorted(layer.keyframes, key=lambda item: int(item.frame)):
                    hold = hold_frames_for_keyframe(kf)
                    hold_end = keyframe_hold_end(kf, layer.keyframes)
                    hold_note = f" · hold {hold}" if hold > 1 or hold_end > int(kf.frame) else ""
                    kf_item = QTreeWidgetItem(layer_item)
                    kf_item.setData(0, Qt.ItemDataRole.UserRole, ("keyframe", layer.id, int(kf.frame)))
                    kf_item.setSizeHint(0, QSize(0, _ROW_KEY_H))
                    kf_row = _DrawStackKeyframeRowWidget(
                        title=f"F{format_frame_label(kf.frame)}",
                        subtitle=f"{len(kf.strokes)} stroke{hold_note}",
                        visible=kf.visible,
                    )
                    kf_row.apply_visibility_display(
                        key_visible=kf.visible,
                        layer_visible=layer.visible,
                    )
                    kf_row.visibility_toggled.connect(
                        lambda lid=layer.id, fr=int(kf.frame): self.keyframe_visibility_toggled.emit(
                            lid, fr
                        )
                    )
                    self._tree.setItemWidget(kf_item, 0, kf_row)
                    if (
                        active_layer_id == layer.id
                        and active_frame is not None
                        and int(kf.frame) == int(active_frame)
                    ):
                        select_item = kf_item
                layer_item.setExpanded(layer_expanded)
                if select_item is None and active_layer_id == layer.id:
                    select_item = layer_item

            if select_item is not None:
                self._tree.setCurrentItem(select_item)
            elif self._tree.topLevelItemCount() > 0:
                self._tree.setCurrentItem(None)
        finally:
            self._tree.blockSignals(False)
            self._selection_guard = False
        if self._edit_popup_enabled:
            self._position_hold_popup()

    def _layer_id_from_item(self, layer_item: QTreeWidgetItem) -> str | None:
        data = layer_item.data(0, Qt.ItemDataRole.UserRole)
        if isinstance(data, tuple) and data[0] == "layer" and isinstance(data[1], str):
            return data[1]
        return None

    def _toggle_layer_expanded(self, layer_item: QTreeWidgetItem) -> None:
        if layer_item.childCount() <= 0:
            return
        layer_id = self._layer_id_from_item(layer_item)
        if layer_id is None:
            return
        expanded = not layer_item.isExpanded()
        if expanded:
            self._expanded_layer_ids.add(layer_id)
        else:
            self._expanded_layer_ids.discard(layer_id)
        layer_item.setExpanded(expanded)

    def _on_layer_item_expansion_changed(self, item: QTreeWidgetItem) -> None:
        if item.parent() is not None:
            return
        layer_id = self._layer_id_from_item(item)
        if layer_id is None:
            return
        if item.isExpanded():
            self._expanded_layer_ids.add(layer_id)
        else:
            self._expanded_layer_ids.discard(layer_id)
        row = self._tree.itemWidget(item, 0)
        if isinstance(row, _DrawStackLayerRowWidget):
            row.set_disclosure_state(expanded=item.isExpanded(), has_keyframes=item.childCount() > 0)

    def _find_keyframe_item(self, layer_id: str, frame: int) -> QTreeWidgetItem | None:
        for row in range(self._tree.topLevelItemCount()):
            layer_item = self._tree.topLevelItem(row)
            if layer_item is None:
                continue
            data = layer_item.data(0, Qt.ItemDataRole.UserRole)
            if not isinstance(data, tuple) or data[0] != "layer" or data[1] != layer_id:
                continue
            for idx in range(layer_item.childCount()):
                child = layer_item.child(idx)
                if child is None:
                    continue
                child_data = child.data(0, Qt.ItemDataRole.UserRole)
                if (
                    isinstance(child_data, tuple)
                    and child_data[0] == "keyframe"
                    and child_data[1] == layer_id
                    and int(child_data[2]) == int(frame)
                ):
                    return child
        return None

    def _position_hold_popup(self) -> None:
        if not self._edit_popup_enabled or not self._edit_layer_id or self._edit_frame is None:
            self._hold_popup.hide()
            return
        item = self._find_keyframe_item(self._edit_layer_id, int(self._edit_frame))
        if item is None:
            self._hold_popup.hide()
            return
        anchor = self._tree.itemWidget(item, 0)
        if anchor is None:
            self._hold_popup.hide()
            return
        self._hold_popup.set_hold(self._edit_hold)
        position_popup_near_anchor(self._hold_popup, anchor, gap=4)
        self._hold_popup.show()

    def _on_current_item_changed(
        self,
        current: QTreeWidgetItem | None,
        _previous: QTreeWidgetItem | None,
    ) -> None:
        if self._selection_guard or current is None:
            return
        data = current.data(0, Qt.ItemDataRole.UserRole)
        if not isinstance(data, tuple) or not data:
            return
        kind = data[0]
        if kind == "layer" and isinstance(data[1], str):
            self.layer_selected.emit(data[1])
            return
        if kind == "keyframe" and len(data) >= 3:
            layer_id = str(data[1])
            frame = int(data[2])
            self.keyframe_selected.emit(layer_id, frame)

    def hideEvent(self, event) -> None:  # noqa: N802
        self._hold_popup.hide()
        super().hideEvent(event)

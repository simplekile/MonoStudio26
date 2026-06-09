"""Compact assignee picker: profile row trigger, multi-select roster popup."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QColor
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.user_identity import (
    StudioUser,
    avatar_path,
    format_assignees_label,
    get_user,
    normalize_assignee_ids,
    read_roster,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.popup_position import DEFAULT_POPUP_MARGIN, position_popup_near_global_point
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio

_ROW_AVATAR_PX = 36
_STACK_AVATAR_PX = 28
_STACK_OVERLAP = 8
_POPUP_AVATAR_PX = 28
_ROW_H = 52
_LIST_W = 336
_MAX_VISIBLE_ROWS = 8
_ROW_MIN_H = 56
_FOOTER_H = 48
_POPUP_BG = "#25282c"


def _assignee_popup_stylesheet() -> str:
    """Scoped QSS on the popup widget — Qt.Popup top-level windows skip app-wide rules on Windows."""
    return f"""
        QFrame#AssigneePickerPopup {{
            background-color: {_POPUP_BG};
            border: 1px solid rgba(255, 255, 255, 0.06);
            border-radius: 8px;
        }}
        QFrame#AssigneePickerPopupFooter {{
            background-color: {_POPUP_BG};
            border: none;
            border-top: 1px solid rgba(255, 255, 255, 0.06);
        }}
        QListWidget#AssigneePickerList {{
            background-color: {_POPUP_BG};
            border: none;
            outline: none;
            padding: 2px;
            font-family: "Inter";
            font-size: 12px;
            font-weight: 500;
            color: #d4d4d8;
        }}
        QListWidget#AssigneePickerList::viewport {{
            background-color: {_POPUP_BG};
        }}
        QListWidget#AssigneePickerList::item {{
            height: 52px;
            padding: 0px 4px;
            border-radius: 6px;
            background: transparent;
            border: none;
        }}
        QListWidget#AssigneePickerList::item:hover {{
            background: rgba(255, 255, 255, 0.06);
        }}
        QListWidget#AssigneePickerList::item:selected {{
            background: rgba(59, 130, 246, 0.12);
            border: 1px solid rgba(59, 130, 246, 0.28);
        }}
        QWidget#AssigneePickerListRow,
        QWidget#AssigneePickerListRow QWidget,
        QWidget#AssigneePickerListRow QLabel {{
            background: transparent;
            border: none;
        }}
        QLabel#AssigneePickerListName {{
            color: #fafafa;
            background: transparent;
        }}
        QLabel#AssigneePickerListMeta {{
            color: #a1a1aa;
            background: transparent;
        }}
    """


def _user_meta_line(user: StudioUser | None) -> str:
    if user is None:
        return "Click to choose assignees"
    email = (user.email or "").strip()
    if email:
        return email
    uid = (user.id or "").strip()
    return uid or "—"


class _AssigneePickerRow(QFrame):
    """Clickable assignee summary: avatar(s), names, chevron."""

    clicked = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("AssigneePickerRow")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(_ROW_MIN_H)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(12)

        self._single_avatar = QLabel(self)
        self._single_avatar.setObjectName("AssigneePickerRowAvatar")
        self._single_avatar.setFixedSize(_ROW_AVATAR_PX, _ROW_AVATAR_PX)
        self._single_avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._single_avatar.setAutoFillBackground(False)

        self._avatar_stack = QWidget(self)
        self._avatar_stack.setObjectName("AssigneePickerAvatarStack")
        self._avatar_stack.setAutoFillBackground(False)
        stack_l = QHBoxLayout(self._avatar_stack)
        stack_l.setContentsMargins(0, 0, 4, 0)
        stack_l.setSpacing(-_STACK_OVERLAP)
        stack_l.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self._avatar_host = QWidget(self)
        self._avatar_host.setObjectName("AssigneePickerAvatarHost")
        self._avatar_host.setAutoFillBackground(False)
        host_l = QHBoxLayout(self._avatar_host)
        host_l.setContentsMargins(0, 0, 0, 0)
        host_l.setSpacing(0)
        host_l.addWidget(self._single_avatar, 0, Qt.AlignmentFlag.AlignVCenter)
        host_l.addWidget(self._avatar_stack, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self._avatar_host, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QWidget(self)
        text_col.setObjectName("AssigneePickerTextCol")
        text_col.setAutoFillBackground(False)
        text_col.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        text_l = QVBoxLayout(text_col)
        text_l.setContentsMargins(0, 0, 0, 0)
        text_l.setSpacing(2)

        self._name = QLabel("", text_col)
        self._name.setObjectName("AssigneePickerName")
        self._name.setFont(monos_font("Inter", 14, QFont.Weight.DemiBold))
        self._name.setAutoFillBackground(False)
        self._name.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        self._meta = QLabel("", text_col)
        self._meta.setObjectName("AssigneePickerMeta")
        self._meta.setFont(monos_font("Inter", 12, QFont.Weight.Medium))
        self._meta.setAutoFillBackground(False)
        self._meta.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._meta.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        text_l.addWidget(self._name)
        text_l.addWidget(self._meta)
        layout.addWidget(text_col, 1, Qt.AlignmentFlag.AlignVCenter)

        self._chevron = QLabel(self)
        self._chevron.setObjectName("AssigneePickerChevron")
        self._chevron.setPixmap(
            lucide_icon("chevron-right", size=16, color_hex=MONOS_COLORS["text_meta"]).pixmap(16, 16)
        )
        self._chevron.setFixedSize(16, 16)
        layout.addWidget(self._chevron, 0, Qt.AlignmentFlag.AlignVCenter)
        self._read_only = False

    def _clear_stack(self) -> None:
        stack_l = self._avatar_stack.layout()
        if stack_l is None:
            return
        while stack_l.count():
            item = stack_l.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

    def set_users_display(
        self,
        users: list[StudioUser],
        *,
        workspace_root: Path | None,
    ) -> None:
        dpr = effective_device_pixel_ratio(self)
        self._clear_stack()
        if not users:
            self._single_avatar.setVisible(True)
            self._avatar_stack.setVisible(False)
            self._single_avatar.setPixmap(
                lucide_icon("user", size=_ROW_AVATAR_PX, color_hex=MONOS_COLORS["text_meta"]).pixmap(
                    _ROW_AVATAR_PX, _ROW_AVATAR_PX
                )
            )
            self._name.setText("Unassigned")
            self._meta.setText("Click to choose, then Apply")
            self.setToolTip("Unassigned — click to assign")
            return

        names = [(u.name or u.id).strip() for u in users if (u.name or u.id).strip()]
        if len(users) == 1:
            user = users[0]
            self._single_avatar.setVisible(True)
            self._avatar_stack.setVisible(False)
            self._single_avatar.setPixmap(
                avatar_pixmap_for(
                    avatar_path(workspace_root, user) if workspace_root else None,
                    user.initials,
                    user.color_hex,
                    _ROW_AVATAR_PX,
                    dpr=dpr,
                )
            )
            self._name.setText(names[0] if names else user.id)
            self._meta.setText(_user_meta_line(user))
            self.setToolTip(names[0] if names else user.id)
            return

        self._single_avatar.setVisible(False)
        self._avatar_stack.setVisible(True)
        stack_l = self._avatar_stack.layout()
        shown = users[:3]
        for user in shown:
            av = QLabel(self._avatar_stack)
            av.setFixedSize(_STACK_AVATAR_PX, _STACK_AVATAR_PX)
            av.setPixmap(
                avatar_pixmap_for(
                    avatar_path(workspace_root, user) if workspace_root else None,
                    user.initials,
                    user.color_hex,
                    _STACK_AVATAR_PX,
                    dpr=dpr,
                )
            )
            if stack_l is not None:
                stack_l.addWidget(av, 0, Qt.AlignmentFlag.AlignVCenter)
        if len(users) > 3:
            more = QLabel(f"+{len(users) - 3}", self._avatar_stack)
            more.setAlignment(Qt.AlignmentFlag.AlignCenter)
            more.setFixedSize(_STACK_AVATAR_PX, _STACK_AVATAR_PX)
            more.setFont(monos_font("Inter", 9, QFont.Weight.DemiBold))
            more.setStyleSheet(
                "color: #a1a1aa; background: rgba(63, 63, 70, 0.55);"
                "border: 1px solid rgba(63, 63, 70, 0.9); border-radius: 999px;"
            )
            if stack_l is not None:
                stack_l.addWidget(more, 0, Qt.AlignmentFlag.AlignVCenter)

        label = format_assignees_label(tuple(names))
        self._name.setText(label)
        count = len(users)
        self._meta.setText(f"{count} assignees")
        self.setToolTip(", ".join(names))

    def set_read_only(self, read_only: bool) -> None:
        self._read_only = bool(read_only)
        if self._read_only:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self._chevron.setVisible(False)
        else:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            self._chevron.setVisible(True)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if self._read_only:
            super().mousePressEvent(event)
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)


class _AssigneeRowWidget(QWidget):
    """One roster row in popup: avatar + name + email, optional check + profile."""

    toggled = Signal(str, bool)
    profile_requested = Signal(str)
    clear_all = Signal()

    def __init__(
        self,
        *,
        user: StudioUser | None,
        workspace_root: Path | None,
        unassigned: bool = False,
        checked: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("AssigneePickerListRow")
        self.setAutoFillBackground(False)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)
        self._user_id = (user.id if user else "").strip()
        self._is_unassigned = unassigned or user is None
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(8)

        if not self._is_unassigned:
            check = QLabel(self)
            check.setFixedSize(16, 16)
            check.setPixmap(
                lucide_icon(
                    "circle-check" if checked else "circle",
                    size=16,
                    color_hex="#60a5fa" if checked else "#52525b",
                ).pixmap(16, 16)
            )
            layout.addWidget(check, 0, Qt.AlignmentFlag.AlignVCenter)

        self._avatar = QLabel(self)
        self._avatar.setFixedSize(_POPUP_AVATAR_PX, _POPUP_AVATAR_PX)
        dpr = effective_device_pixel_ratio(self)
        if self._is_unassigned:
            self._avatar.setPixmap(
                lucide_icon("user", size=_POPUP_AVATAR_PX, color_hex=MONOS_COLORS["text_meta"]).pixmap(
                    _POPUP_AVATAR_PX, _POPUP_AVATAR_PX
                )
            )
            name_text = "Clear assignees"
            meta_text = "Remove everyone from this task"
        else:
            assert user is not None
            self._avatar.setPixmap(
                avatar_pixmap_for(
                    avatar_path(workspace_root, user) if workspace_root else None,
                    user.initials,
                    user.color_hex,
                    _POPUP_AVATAR_PX,
                    dpr=dpr,
                )
            )
            name_text = (user.name or user.id).strip()
            meta_text = _user_meta_line(user)

        layout.addWidget(self._avatar, 0, Qt.AlignmentFlag.AlignVCenter)

        text_col = QWidget(self)
        text_col.setObjectName("AssigneePickerListTextCol")
        text_col.setAutoFillBackground(False)
        text_col.setMinimumWidth(0)
        text_l = QVBoxLayout(text_col)
        text_l.setContentsMargins(0, 0, 0, 0)
        text_l.setSpacing(2)

        name = QLabel(name_text, text_col)
        name.setObjectName("AssigneePickerListName")
        name.setFont(monos_font("Inter", 12, QFont.Weight.DemiBold))
        name.setAutoFillBackground(False)
        name.setMinimumWidth(0)
        name.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        meta = QLabel(meta_text, text_col)
        meta.setObjectName("AssigneePickerListMeta")
        meta.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        meta.setAutoFillBackground(False)
        meta.setMinimumWidth(0)
        meta.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        text_l.addWidget(name)
        text_l.addWidget(meta)
        layout.addWidget(text_col, 1, Qt.AlignmentFlag.AlignVCenter)

        self._name_full = name_text
        self._meta_full = meta_text
        self._name_label = name
        self._meta_label = meta
        if meta_text:
            meta.setToolTip(meta_text)
        if name_text and name_text != meta_text:
            name.setToolTip(name_text)

        if user is not None:
            profile_btn = QPushButton(self)
            profile_btn.setObjectName("AssigneePickerProfileBtn")
            profile_btn.setToolTip("View profile")
            profile_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            profile_btn.setFixedSize(28, 28)
            profile_btn.setIcon(lucide_icon("user", size=16, color_hex="#a1a1aa"))
            profile_btn.setIconSize(QSize(16, 16))
            uid = user.id
            profile_btn.clicked.connect(lambda _checked=False, u=uid: self.profile_requested.emit(u))
            layout.addWidget(profile_btn, 0, Qt.AlignmentFlag.AlignVCenter)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            child = self.childAt(event.pos())
            if isinstance(child, QPushButton) and child.objectName() == "AssigneePickerProfileBtn":
                super().mousePressEvent(event)
                return
            if self._is_unassigned:
                self.clear_all.emit()
            else:
                self.toggled.emit(self._user_id, True)
            event.accept()
            return
        super().mousePressEvent(event)

    def resizeEvent(self, event) -> None:  # noqa: N802
        super().resizeEvent(event)
        self._sync_text_elide()

    def _sync_text_elide(self) -> None:
        name_w = max(48, self._name_label.width())
        meta_w = max(48, self._meta_label.width())
        name_fm = QFontMetrics(self._name_label.font())
        meta_fm = QFontMetrics(self._meta_label.font())
        self._name_label.setText(
            name_fm.elidedText(self._name_full, Qt.TextElideMode.ElideRight, name_w)
        )
        self._meta_label.setText(
            meta_fm.elidedText(self._meta_full, Qt.TextElideMode.ElideRight, meta_w)
        )


class AssigneePickerPopup(QFrame):
    """Roster multi-select popup; assignees apply only after footer confirm."""

    selection_changed = Signal(list)

    def __init__(self, parent=None, *, workspace_root: Path | None = None) -> None:
        flags = Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint | Qt.WindowType.NoDropShadowWindowHint
        super().__init__(parent, flags)
        self.setObjectName("AssigneePickerPopup")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)
        self._workspace_root: Path | None = None
        self._draft: set[str] = set()
        self.set_workspace_root(workspace_root)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 6)
        layout.setSpacing(0)

        self._list = QListWidget(self)
        self._list.setObjectName("AssigneePickerList")
        self._list.setFrameShape(QFrame.Shape.NoFrame)
        self._list.setAutoFillBackground(False)
        self._list.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self._list.setUniformItemSizes(True)
        self._list.setSpacing(0)
        layout.addWidget(self._list)

        footer = QFrame(self)
        footer.setObjectName("AssigneePickerPopupFooter")
        footer.setMinimumHeight(_FOOTER_H)
        footer_l = QHBoxLayout(footer)
        footer_l.setContentsMargins(8, 6, 8, 8)
        footer_l.setSpacing(8)

        self._btn_cancel = QPushButton("Cancel", footer)
        self._btn_cancel.setObjectName("DialogSecondaryButton")
        self._btn_cancel.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_apply = QPushButton("Apply", footer)
        self._btn_apply.setObjectName("DialogPrimaryButton")
        self._btn_apply.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_cancel.clicked.connect(self.hide)
        self._btn_apply.clicked.connect(self._on_apply)
        footer_l.addStretch(1)
        footer_l.addWidget(self._btn_cancel, 0)
        footer_l.addWidget(self._btn_apply, 0)
        layout.addWidget(footer)

        self.setFixedWidth(_LIST_W)
        self.setStyleSheet(_assignee_popup_stylesheet())
        self.setAutoFillBackground(False)
        footer.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        footer.setAutoFillBackground(False)
        self._list.viewport().setAutoFillBackground(False)

    def paintEvent(self, event) -> None:  # noqa: N802
        """Popup top-level windows may not paint QSS background on Windows."""
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = self.rect()
        p.setPen(QColor(255, 255, 255, 15))
        p.setBrush(QColor(_POPUP_BG))
        p.drawRoundedRect(r.adjusted(0, 0, -1, -1), 8, 8)
        p.end()
        super().paintEvent(event)

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        for i in range(self._list.count()):
            row = self._list.itemWidget(self._list.item(i))
            if isinstance(row, _AssigneeRowWidget):
                row._sync_text_elide()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
            event.accept()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._on_apply()
            event.accept()
            return
        super().keyPressEvent(event)

    def set_workspace_root(self, workspace_root: Path | None) -> None:
        try:
            self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        except OSError:
            self._workspace_root = None

    def set_selection(self, user_ids: list[str] | tuple[str, ...]) -> None:
        self._draft = set(normalize_assignee_ids(user_ids))

    def selection(self) -> list[str]:
        return list(normalize_assignee_ids(tuple(self._draft)))

    def populate(self) -> None:
        self._list.clear()
        clear_item = QListWidgetItem()
        clear_item.setSizeHint(QSize(0, _ROW_H))
        self._list.addItem(clear_item)
        clear_row = _AssigneeRowWidget(
            user=None,
            workspace_root=self._workspace_root,
            unassigned=True,
            parent=self._list,
        )
        clear_row.clear_all.connect(self._on_clear_all)
        self._list.setItemWidget(clear_item, clear_row)

        users = [
            u for u in read_roster(self._workspace_root)
            if u.active and (u.name or "").strip()
        ]
        users.sort(key=lambda u: (u.name or "").casefold())
        for user in users:
            item = QListWidgetItem()
            item.setSizeHint(QSize(0, _ROW_H))
            self._list.addItem(item)
            uid = user.id
            checked = uid in self._draft
            row = _AssigneeRowWidget(
                user=user,
                workspace_root=self._workspace_root,
                checked=checked,
                parent=self._list,
            )
            row.toggled.connect(self._on_user_toggled)
            row.profile_requested.connect(self._on_profile_requested)
            self._list.setItemWidget(item, row)

        count = self._list.count()
        visible = min(max(count, 1), _MAX_VISIBLE_ROWS)
        list_h = visible * _ROW_H + 8
        self._list.setFixedHeight(list_h)
        self.adjustSize()

    def _on_user_toggled(self, user_id: str, _checked: bool) -> None:
        uid = (user_id or "").strip()
        if not uid:
            return
        if uid in self._draft:
            self._draft.discard(uid)
        else:
            self._draft.add(uid)
        self.populate()

    def _on_clear_all(self) -> None:
        self._draft.clear()
        self.populate()

    def _on_apply(self) -> None:
        self.selection_changed.emit(self.selection())
        self.hide()

    def open_near(
        self,
        global_anchor: QPoint,
        *,
        anchor_rect_global: QRect | None = None,
    ) -> None:
        self.setFixedWidth(_LIST_W)
        self.populate()
        position_popup_near_global_point(
            self,
            global_anchor,
            anchor_rect_global=anchor_rect_global,
            margin=DEFAULT_POPUP_MARGIN,
            max_width=_LIST_W,
        )
        self.show()
        self.raise_()
        self.activateWindow()
        self._btn_apply.setFocus(Qt.FocusReason.PopupFocusReason)

    def _on_profile_requested(self, user_id: str) -> None:
        from monostudio.ui_qt.user_profile_view_dialog import open_studio_user_profile

        open_studio_user_profile(self._workspace_root, user_id, parent=self.window())


class AssigneePickerWidget(QWidget):
    """Profile-row assignee control with multi-select roster popup."""

    users_changed = Signal(list)

    def __init__(self, workspace_root: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._workspace_root: Path | None = None
        self._selected_ids: tuple[str, ...] = ()
        self._sync = False

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        self._row = _AssigneePickerRow(self)
        self._row.clicked.connect(self._open_popup)
        layout.addWidget(self._row, 1)

        self._popup = AssigneePickerPopup(self.window(), workspace_root=workspace_root)
        self._popup.selection_changed.connect(self._on_popup_selection_changed)

        self.set_workspace_root(workspace_root)
        self.set_user_ids([])

    def set_workspace_root(self, workspace_root: Path | None) -> None:
        prev = list(self._selected_ids)
        try:
            self._workspace_root = Path(workspace_root).resolve() if workspace_root else None
        except OSError:
            self._workspace_root = None
        self._popup.set_workspace_root(self._workspace_root)
        if prev:
            self.set_user_ids(prev)

    def selected_user_ids(self) -> list[str]:
        return list(self._selected_ids)

    def selected_user_id(self) -> str | None:
        return self._selected_ids[0] if self._selected_ids else None

    def set_user_id(self, user_id: str | None) -> None:
        uid = (user_id or "").strip()
        self.set_user_ids([uid] if uid else [])

    def set_user_ids(self, user_ids: list[str] | tuple[str, ...] | None) -> None:
        self._sync = True
        try:
            self._selected_ids = normalize_assignee_ids(user_ids)
            users: list[StudioUser] = []
            for uid in self._selected_ids:
                user = get_user(self._workspace_root, uid)
                if user is None:
                    for u in read_roster(self._workspace_root):
                        if u.id == uid:
                            user = u
                            break
                if user is not None:
                    users.append(user)
            self._row.set_users_display(users, workspace_root=self._workspace_root)
        finally:
            self._sync = False

    def set_read_only(self, read_only: bool) -> None:
        self._row.set_read_only(read_only)

    def _open_popup(self) -> None:
        if self._row._read_only:
            return
        self._popup.set_selection(self._selected_ids)
        row = self._row
        top_left = row.mapToGlobal(QPoint(0, 0))
        bottom_right = row.mapToGlobal(QPoint(row.width(), row.height()))
        anchor_rect = QRect(top_left, bottom_right).normalized()
        bottom_left = QPoint(top_left.x(), bottom_right.y() + 4)
        self._popup.open_near(bottom_left, anchor_rect_global=anchor_rect)

    def _on_popup_selection_changed(self, user_ids: list[str]) -> None:
        if self._sync:
            return
        new_ids = normalize_assignee_ids(user_ids)
        if new_ids == self._selected_ids:
            return
        self._selected_ids = new_ids
        self._row.set_users_display(
            self._users_for_ids(self._selected_ids),
            workspace_root=self._workspace_root,
        )
        self.users_changed.emit(list(self._selected_ids))

    def _users_for_ids(self, ids: tuple[str, ...]) -> list[StudioUser]:
        users: list[StudioUser] = []
        for uid in ids:
            user = get_user(self._workspace_root, uid)
            if user is None:
                for u in read_roster(self._workspace_root):
                    if u.id == uid:
                        user = u
                        break
            if user is not None:
                users.append(user)
        return users

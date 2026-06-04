"""Self-service profile editor for the signed-in studio user."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor, QFont, QMouseEvent, QPainter, QPen
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.user_identity import (
    StudioUser,
    avatar_path,
    change_password,
    get_user,
    has_password,
    studio_role_label,
    update_user_profile,
    user_color_choices,
)
from monostudio.ui_qt.style import MonosDialog, monos_font
from monostudio.ui_qt.user_avatar import ProfileAvatarLabel, avatar_pixmap, save_avatar_image


class _ColorSwatch(QWidget):
    """Painted swatch — QSS background on MonosDialog children is unreliable (translucent dialog)."""

    clicked = Signal(str)

    def __init__(self, color_hex: str, *, selected: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("UserProfileColorSwatch")
        self._color = color_hex
        self._selected = selected
        self.setFixedSize(28, 28)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setProperty("selected", "true" if selected else "false")

    def set_selected(self, on: bool) -> None:
        self._selected = on
        self.setProperty("selected", "true" if on else "false")
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = QColor(self._color)
        if not fill.isValid():
            fill = QColor("#3b82f6")
        side = min(self.width(), self.height())
        inset = 2.0 if self._selected else 1.0
        r = inset
        rect = (r, r, side - 2 * inset, side - 2 * inset)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(fill)
        p.drawRoundedRect(*rect, 6.0, 6.0)
        if self._selected:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.setPen(QPen(QColor("#fafafa"), 2))
        else:
            p.setPen(QPen(QColor("#52525b"), 1))
        p.drawRoundedRect(*rect, 6.0, 6.0)
        p.end()

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._color)
            event.accept()
            return
        super().mousePressEvent(event)


class UserProfileDialog(MonosDialog):
    def __init__(self, *, workspace_root: Path, user: StudioUser, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("UserProfileDialog")
        self.setWindowTitle("My profile")
        self.setModal(True)
        self.setMinimumWidth(400)

        self._workspace_root = Path(workspace_root)
        self._user = user
        self._avatar_src: Path | None = avatar_path(workspace_root, user)
        self._pending_avatar_file: str | None = None
        self._clear_avatar = False
        self._color_hex = user.color_hex
        self._initial_color_hex = user.color_hex
        self._swatches: list[_ColorSwatch] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        # Avatar
        av_row = QHBoxLayout()
        av_row.setSpacing(12)
        self._avatar_lbl = ProfileAvatarLabel(
            photo_path=self._avatar_src if not self._clear_avatar else None,
            initials=user.initials,
            color_hex=self._color_hex,
            size=64,
            object_name="UserProfileViewAvatar",
            parent=self,
        )
        self._refresh_avatar_preview()
        av_row.addWidget(self._avatar_lbl, 0, Qt.AlignVCenter)
        av_btns = QVBoxLayout()
        av_btns.setSpacing(6)
        pick = QPushButton("Choose photo…", self)
        pick.setObjectName("DialogSecondaryButton")
        pick.clicked.connect(self._pick_avatar)
        remove = QPushButton("Remove photo", self)
        remove.setObjectName("DialogSecondaryButton")
        remove.clicked.connect(self._remove_avatar)
        remove.setEnabled(bool(user.avatar or self._avatar_src))
        self._remove_btn = remove
        av_btns.addWidget(pick)
        av_btns.addWidget(remove)
        av_btns.addStretch(1)
        av_row.addLayout(av_btns, 1)
        layout.addLayout(av_row)

        layout.addWidget(self._label("Display name"))
        self._name = QLineEdit(user.name, self)
        self._name.textChanged.connect(self._refresh_avatar_preview)
        layout.addWidget(self._name)

        layout.addWidget(self._label("Email"))
        self._email = QLineEdit(user.email, self)
        self._email.setPlaceholderText("Optional")
        layout.addWidget(self._email)

        layout.addWidget(self._label("Accent color (initials)"))
        color_row = QHBoxLayout()
        color_row.setSpacing(8)
        self._initials_preview = QLabel(self)
        self._initials_preview.setFixedSize(40, 40)
        self._initials_preview.setToolTip("Preview — initials avatar uses this color")
        color_row.addWidget(self._initials_preview, 0, Qt.AlignVCenter)
        for c in user_color_choices():
            sw = _ColorSwatch(c, selected=(c.lower() == user.color_hex.lower()), parent=self)
            sw.clicked.connect(self._on_color)
            color_row.addWidget(sw)
            self._swatches.append(sw)
        color_row.addStretch(1)
        layout.addLayout(color_row)
        self._color_hint = QLabel(
            "Used for the initials circle when you have no profile photo, or after Remove photo.",
            self,
        )
        self._color_hint.setObjectName("DialogHint")
        self._color_hint.setWordWrap(True)
        layout.addWidget(self._color_hint)

        role_l = QLabel(
            f"Role: {studio_role_label(user.role)} — ask an admin to change in Team management.",
            self,
        )
        role_l.setObjectName("DialogHint")
        role_l.setWordWrap(True)
        layout.addWidget(role_l)

        layout.addWidget(self._sep_label("Change password"))

        self._cur_pw = QLineEdit(self)
        self._cur_pw.setEchoMode(QLineEdit.Password)
        self._cur_pw.setPlaceholderText(
            "Current password" if has_password(user) else "Not required (no password yet)"
        )
        self._cur_pw.setEnabled(has_password(user))
        layout.addWidget(self._cur_pw)

        self._new_pw = QLineEdit(self)
        self._new_pw.setEchoMode(QLineEdit.Password)
        self._new_pw.setPlaceholderText("New password (leave blank to keep)")
        layout.addWidget(self._new_pw)

        self._new_pw2 = QLineEdit(self)
        self._new_pw2.setEchoMode(QLineEdit.Password)
        self._new_pw2.setPlaceholderText("Confirm new password")
        layout.addWidget(self._new_pw2)

        self._error = QLabel("", self)
        self._error.setObjectName("DialogWarning")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        save = QPushButton("Save changes", self)
        save.setObjectName("DialogPrimaryButton")
        save.setDefault(True)
        save.clicked.connect(self._on_save)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("DialogSecondaryButton")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(save)
        btn_row.addWidget(cancel)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)
        self._refresh_avatar_preview()

    @staticmethod
    def _label(text: str) -> QLabel:
        lab = QLabel(text.upper(), parent=None)
        lab.setObjectName("DialogHint")
        lab.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
        return lab

    @staticmethod
    def _sep_label(text: str) -> QLabel:
        lab = QLabel(text, parent=None)
        lab.setObjectName("DashboardCardTitle")
        return lab

    def _refresh_avatar_preview(self) -> None:
        name = self._name.text().strip() if hasattr(self, "_name") else self._user.name
        initials = (name[:2].upper() if name else self._user.initials)
        src = None if self._clear_avatar else self._avatar_src
        self._avatar_lbl.update_display(
            photo_path=src,
            initials=initials,
            color_hex=self._color_hex,
            size=64,
        )
        if hasattr(self, "_initials_preview"):
            self._initials_preview.setPixmap(
                avatar_pixmap(initials, self._color_hex, 40)
            )
        has_photo = src is not None and Path(src).is_file() and not self._clear_avatar
        if hasattr(self, "_color_hint"):
            if has_photo:
                self._color_hint.setText(
                    "Your photo is shown above. Accent color applies after Remove photo "
                    "(see preview on the left)."
                )
            else:
                self._color_hint.setText(
                    "Used for the initials circle when you have no profile photo."
                )

    def _pick_avatar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose profile photo", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path:
            self._avatar_src = Path(path)
            self._clear_avatar = False
            self._remove_btn.setEnabled(True)
            self._refresh_avatar_preview()

    def _remove_avatar(self) -> None:
        self._avatar_src = None
        self._pending_avatar_file = None
        self._clear_avatar = True
        self._remove_btn.setEnabled(False)
        self._refresh_avatar_preview()

    def _on_color(self, color_hex: str) -> None:
        self._color_hex = color_hex
        for sw in self._swatches:
            sw.set_selected(sw._color.lower() == color_hex.lower())
        self._refresh_avatar_preview()

    def _fail(self, msg: str) -> None:
        self._error.setText(msg)
        self._error.setVisible(True)

    def _on_save(self) -> None:
        name = self._name.text().strip()
        if not name:
            return self._fail("Display name is required.")

        new_pw = self._new_pw.text().strip()
        new_pw2 = self._new_pw2.text().strip()
        if new_pw or new_pw2:
            if new_pw != new_pw2:
                return self._fail("New passwords do not match.")
            err = change_password(
                self._workspace_root,
                self._user.id,
                self._cur_pw.text(),
                new_pw,
            )
            if err:
                return self._fail(err)

        avatar_filename: str | None = None
        if self._clear_avatar:
            pass  # handled by clear_avatar flag
        elif self._avatar_src is not None:
            try:
                if self._avatar_src != avatar_path(self._workspace_root, self._user):
                    avatar_filename = save_avatar_image(
                        self._workspace_root, self._user.id, self._avatar_src
                    )
                else:
                    avatar_filename = self._user.avatar
            except (OSError, ValueError) as ex:
                return self._fail(f"Could not save photo: {ex}")

        updated = update_user_profile(
            self._workspace_root,
            self._user.id,
            name=name,
            email=self._email.text(),
            color_hex=self._color_hex,
            avatar=avatar_filename,
            clear_avatar=self._clear_avatar,
        )
        if updated is None:
            return self._fail("Could not save profile.")
        self._user = updated
        self.accept()

    def accent_color_changed(self) -> bool:
        return self._color_hex.lower() != self._initial_color_hex.lower()

    def updated_user(self) -> StudioUser:
        return get_user(self._workspace_root, self._user.id) or self._user

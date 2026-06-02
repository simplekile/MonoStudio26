"""Sign-in (and account-request / admin-create) for the Dropbox studio roster.

- ``UserIdentityDialog``  : pick a user + password to sign in on this machine.
- ``RequestAccountDialog``: submit an account request (non-admin) or create a
  user directly (admin / first-account bootstrap).
"""

from __future__ import annotations

import uuid
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.user_identity import (
    StudioUser,
    active_users,
    avatar_path,
    find_user_by_device,
    has_password,
    hash_password,
    new_user,
    set_password,
    set_user_avatar,
    studio_role_label,
    submit_request,
    upsert_user,
    verify_password,
)
from monostudio.ui_qt.style import MonosDialog, monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, save_avatar_image


class _UserRow(QWidget):
    clicked = Signal(str)

    def __init__(self, user: StudioUser, workspace_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("IdentityUserRow")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._user_id = user.id
        self.setProperty("selected", "false")
        row = QHBoxLayout(self)
        row.setContentsMargins(10, 8, 10, 8)
        row.setSpacing(10)
        av = QLabel(self)
        av.setFixedSize(32, 32)
        av.setPixmap(avatar_pixmap_for(avatar_path(workspace_root, user), user.initials, user.color_hex, 32))
        row.addWidget(av, 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(user.name, self)
        name.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        name.setStyleSheet("color: #fafafa; background: transparent;")
        col.addWidget(name)
        meta_text = studio_role_label(user.role)
        if user.departments:
            meta_text += "  ·  " + ", ".join(user.departments[:3])
        if not has_password(user):
            meta_text += "  ·  set password"
        meta = QLabel(meta_text, self)
        meta.setObjectName("DialogHint")
        col.addWidget(meta)
        row.addLayout(col, 1)

    def set_selected(self, on: bool) -> None:
        self.setProperty("selected", "true" if on else "false")
        st = self.style()
        if st is not None:
            st.unpolish(self)
            st.polish(self)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._user_id)
        super().mouseReleaseEvent(event)


class UserIdentityDialog(MonosDialog):
    def __init__(self, *, workspace_root: Path, is_admin: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("UserIdentityDialog")
        self.setWindowTitle("Sign in")
        self.setModal(True)
        self.setMinimumWidth(400)

        self._workspace_root = Path(workspace_root)
        self._is_admin = bool(is_admin)
        self._selected_id: str | None = None
        self._remember = True
        self._rows: list[_UserRow] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        hint = QLabel("Select your account and sign in on this machine.", self)
        hint.setObjectName("DialogHint")
        layout.addWidget(hint)

        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QScrollArea.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._scroll.setMinimumHeight(160)
        self._list_host = QWidget()
        self._list = QVBoxLayout(self._list_host)
        self._list.setContentsMargins(0, 0, 0, 0)
        self._list.setSpacing(4)
        self._scroll.setWidget(self._list_host)
        layout.addWidget(self._scroll, 1)

        self._empty = QLabel("No accounts yet.", self)
        self._empty.setObjectName("DialogHint")
        self._empty.setWordWrap(True)
        layout.addWidget(self._empty)

        # Password (+ confirm shown only when the account has no password yet)
        self._pwd = QLineEdit(self)
        self._pwd.setEchoMode(QLineEdit.Password)
        self._pwd.setPlaceholderText("Password")
        self._pwd.returnPressed.connect(self._try_sign_in)
        layout.addWidget(self._pwd)
        self._pwd2 = QLineEdit(self)
        self._pwd2.setEchoMode(QLineEdit.Password)
        self._pwd2.setPlaceholderText("Confirm new password")
        self._pwd2.returnPressed.connect(self._try_sign_in)
        self._pwd2.setVisible(False)
        layout.addWidget(self._pwd2)

        self._error = QLabel("", self)
        self._error.setObjectName("DialogWarning")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)

        self._remember_cb = QCheckBox("Remember this device (stay signed in)", self)
        self._remember_cb.setChecked(True)
        layout.addWidget(self._remember_cb)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._ok_btn = QPushButton("Sign in", self)
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._try_sign_in)
        self._secondary_btn = QPushButton(
            "Add user…" if self._is_admin else "Request account…", self
        )
        self._secondary_btn.setObjectName("DialogSecondaryButton")
        self._secondary_btn.clicked.connect(self._on_secondary)
        skip_btn = QPushButton("Skip", self)
        skip_btn.setObjectName("DialogSecondaryButton")
        skip_btn.clicked.connect(self.reject)
        btn_row.addWidget(self._ok_btn)
        btn_row.addWidget(self._secondary_btn)
        btn_row.addWidget(skip_btn)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

        self._reload_users()

    # --- data ---------------------------------------------------------------
    def _reload_users(self, *, select_id: str | None = None) -> None:
        while self._list.count():
            item = self._list.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()
        self._rows = []
        users = active_users(self._workspace_root)
        self._empty.setVisible(not users)
        # Bootstrap: with an empty roster anyone may create the first account.
        self._secondary_btn.setText(
            "Add user…" if (self._is_admin or not users) else "Request account…"
        )
        for user in users:
            row = _UserRow(user, self._workspace_root, self._list_host)
            row.clicked.connect(self._on_row_clicked)
            self._list.addWidget(row)
            self._rows.append(row)
        self._list.addStretch(1)
        pre = select_id or self._preselect_id(users)
        if pre:
            self._on_row_clicked(pre)

    def _preselect_id(self, users: list[StudioUser]) -> str | None:
        # Known device → pre-select that account (still needs password).
        u = find_user_by_device(self._workspace_root)
        if u is not None:
            return u.id
        return users[0].id if len(users) == 1 else None

    def _on_row_clicked(self, user_id: str) -> None:
        self._selected_id = user_id
        for row in self._rows:
            row.set_selected(row._user_id == user_id)
        from monostudio.core.user_identity import get_user

        user = get_user(self._workspace_root, user_id)
        need_set = not has_password(user)
        self._pwd.setPlaceholderText("New password" if need_set else "Password")
        self._pwd2.setVisible(need_set)
        self._error.setVisible(False)
        self._ok_btn.setEnabled(True)
        self._ok_btn.setText("Set password & sign in" if need_set else "Sign in")
        self._pwd.setFocus()

    # --- actions ------------------------------------------------------------
    def _try_sign_in(self) -> None:
        from monostudio.core.user_identity import get_user

        if not self._selected_id:
            return
        user = get_user(self._workspace_root, self._selected_id)
        if user is None:
            return
        pw = self._pwd.text()
        if not has_password(user):
            if not pw:
                return self._fail("Enter a new password.")
            if pw != self._pwd2.text():
                return self._fail("Passwords do not match.")
            set_password(self._workspace_root, user.id, pw)
        elif not verify_password(pw, user.pwd_hash):
            return self._fail("Wrong password.")
        self._remember = self._remember_cb.isChecked()
        self.accept()

    def _fail(self, msg: str) -> None:
        self._error.setText(msg)
        self._error.setVisible(True)

    def _on_secondary(self) -> None:
        admin_create = self._is_admin or not active_users(self._workspace_root)
        dlg = RequestAccountDialog(
            workspace_root=self._workspace_root, admin_create=admin_create, parent=self
        )
        if dlg.exec() != RequestAccountDialog.DialogCode.Accepted:
            return
        if admin_create:
            self._reload_users(select_id=dlg.created_user_id())
        else:
            self._error.setObjectName("DialogHint")
            self._error.setText("Request submitted — an admin will review it.")
            self._error.setVisible(True)

    # --- results ------------------------------------------------------------
    def selected_user_id(self) -> str | None:
        return self._selected_id

    def remember(self) -> bool:
        return self._remember


class RequestAccountDialog(MonosDialog):
    """Submit an account request, or (admin/bootstrap) create the user directly."""

    def __init__(self, *, workspace_root: Path, admin_create: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("RequestAccountDialog")
        self.setWindowTitle("Add user" if admin_create else "Request account")
        self.setModal(True)
        self.setMinimumWidth(360)

        self._workspace_root = Path(workspace_root)
        self._admin_create = bool(admin_create)
        self._avatar_src: Path | None = None
        self._created_user_id: str | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        title = QLabel(
            "Create a studio account." if admin_create
            else "Request a studio account (an admin must approve it).",
            self,
        )
        title.setObjectName("DialogHint")
        title.setWordWrap(True)
        layout.addWidget(title)

        # Avatar picker
        av_row = QHBoxLayout()
        av_row.setSpacing(10)
        self._avatar_lbl = QLabel(self)
        self._avatar_lbl.setFixedSize(48, 48)
        self._refresh_avatar_preview()
        av_row.addWidget(self._avatar_lbl, 0, Qt.AlignVCenter)
        pick_btn = QPushButton("Choose photo…", self)
        pick_btn.setObjectName("DialogSecondaryButton")
        pick_btn.clicked.connect(self._pick_avatar)
        av_row.addWidget(pick_btn, 0, Qt.AlignVCenter)
        av_row.addStretch(1)
        layout.addLayout(av_row)

        self._name = QLineEdit(self)
        self._name.setPlaceholderText("Full name")
        self._name.textChanged.connect(self._update_ok)
        layout.addWidget(self._name)
        self._email = QLineEdit(self)
        self._email.setPlaceholderText("Email (optional)")
        layout.addWidget(self._email)
        self._role_combo = None
        if admin_create:
            from monostudio.ui_qt.team_management_dialog import _make_role_combo

            role_hint = QLabel("ROLE", self)
            role_hint.setObjectName("DialogHint")
            role_hint.setFont(monos_font("Inter", 10, QFont.Weight.DemiBold))
            layout.addWidget(role_hint)
            self._role_combo = _make_role_combo("artist", self)
            layout.addWidget(self._role_combo)
        self._pw = QLineEdit(self)
        self._pw.setEchoMode(QLineEdit.Password)
        self._pw.setPlaceholderText("Password")
        self._pw.textChanged.connect(self._update_ok)
        layout.addWidget(self._pw)
        self._pw2 = QLineEdit(self)
        self._pw2.setEchoMode(QLineEdit.Password)
        self._pw2.setPlaceholderText("Confirm password")
        self._pw2.textChanged.connect(self._update_ok)
        layout.addWidget(self._pw2)

        self._error = QLabel("", self)
        self._error.setObjectName("DialogWarning")
        self._error.setWordWrap(True)
        self._error.setVisible(False)
        layout.addWidget(self._error)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)
        self._ok_btn = QPushButton("Create" if admin_create else "Submit request", self)
        self._ok_btn.setObjectName("DialogPrimaryButton")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._on_accept)
        cancel = QPushButton("Cancel", self)
        cancel.setObjectName("DialogSecondaryButton")
        cancel.clicked.connect(self.reject)
        btn_row.addWidget(self._ok_btn)
        btn_row.addWidget(cancel)
        btn_row.addStretch(1)
        layout.addLayout(btn_row)

    def _refresh_avatar_preview(self) -> None:
        from monostudio.ui_qt.user_avatar import avatar_pixmap_for

        initials = (self._name.text().strip()[:2].upper() if hasattr(self, "_name") else "") or "?"
        self._avatar_lbl.setPixmap(
            avatar_pixmap_for(self._avatar_src, initials, "#3b82f6", 48)
        )

    def _pick_avatar(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose profile photo", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if path:
            self._avatar_src = Path(path)
            self._refresh_avatar_preview()

    def _update_ok(self) -> None:
        self._refresh_avatar_preview()
        ok = bool(self._name.text().strip()) and bool(self._pw.text()) and self._pw.text() == self._pw2.text()
        self._ok_btn.setEnabled(ok)

    def _on_accept(self) -> None:
        name = self._name.text().strip()
        pw = self._pw.text()
        if not name or not pw or pw != self._pw2.text():
            return
        avatar_filename = ""
        try:
            if self._avatar_src is not None:
                token = "req_" + uuid.uuid4().hex[:6]
                avatar_filename = save_avatar_image(self._workspace_root, token, self._avatar_src)
        except (OSError, ValueError) as ex:
            self._error.setText(f"Avatar: {ex}")
            self._error.setVisible(True)
            return

        if self._admin_create:
            role = "artist"
            if self._role_combo is not None:
                role = str(self._role_combo.currentData() or "artist")
            user = new_user(name, email=self._email.text().strip(), role=role)
            from monostudio.core.user_identity import _replace_user

            user = _replace_user(user, pwd_hash=hash_password(pw), avatar=avatar_filename)
            upsert_user(self._workspace_root, user)
            if avatar_filename:
                # Re-key avatar file to the user id for tidiness.
                try:
                    final = save_avatar_image(self._workspace_root, user.id, self._avatar_src)
                    set_user_avatar(self._workspace_root, user.id, final)
                except (OSError, ValueError):
                    pass
            self._created_user_id = user.id
        else:
            submit_request(
                self._workspace_root,
                name,
                email=self._email.text().strip(),
                pwd_hash=hash_password(pw),
                avatar=avatar_filename,
            )
        self.accept()

    def created_user_id(self) -> str | None:
        return self._created_user_id

"""Admin-only team management: approve/reject account requests, manage users.

Caller must verify admin capability (``access_control.is_admin_capable()``)
before opening this dialog.
"""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.user_identity import (
    AccountRequest,
    StudioUser,
    approve_request,
    avatar_path,
    deactivate_user,
    delete_user,
    get_current_user,
    normalize_studio_role,
    read_requests,
    read_roster,
    reject_request,
    set_password,
    set_user_avatar,
    set_user_role,
    studio_role_choices,
    studio_role_label,
    upsert_user,
    _replace_user,
)
from monostudio.ui_qt.delete_confirm_dialog import ask_delete
from monostudio.ui_qt.style import MonosDialog, monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, save_avatar_image


def _section_title(text: str) -> QLabel:
    lab = QLabel(text)
    lab.setObjectName("DashboardCardTitle")
    return lab


def _make_role_combo(current: str, parent: QWidget) -> QComboBox:
    combo = QComboBox(parent)
    combo.setObjectName("TeamRoleCombo")
    roles = list(studio_role_choices())
    norm = normalize_studio_role(current)
    if norm not in roles:
        roles.insert(0, norm)
    for r in roles:
        combo.addItem(studio_role_label(r), r)
    idx = combo.findData(norm)
    combo.setCurrentIndex(idx if idx >= 0 else 0)
    combo.setMinimumWidth(112)
    combo.setMaximumWidth(140)
    combo.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    combo.setToolTip("Studio role (display only — not app admin access)")
    return combo


def _team_action_button(text: str, parent: QWidget, *, primary: bool = False) -> QPushButton:
    btn = QPushButton(text, parent)
    btn.setObjectName("DialogPrimaryButton" if primary else "DialogSecondaryButton")
    btn.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
    return btn


class TeamManagementDialog(MonosDialog):
    def __init__(self, *, workspace_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("TeamManagementDialog")
        self.setWindowTitle("Team management")
        self.setModal(True)
        self.setMinimumSize(480, 480)
        self.resize(620, 520)

        self._workspace_root = Path(workspace_root)

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        host = QWidget()
        host.setSizePolicy(QSizePolicy.Policy.MinimumExpanding, QSizePolicy.Policy.Preferred)
        self._body = QVBoxLayout(host)
        self._body.setContentsMargins(0, 0, 0, 0)
        self._body.setSpacing(8)
        scroll.setWidget(host)
        root.addWidget(scroll, 1)

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("DialogSecondaryButton")
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        root.addLayout(close_row)

        self._rebuild()

    # --- build --------------------------------------------------------------
    def _rebuild(self) -> None:
        while self._body.count():
            item = self._body.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setParent(None)
                w.deleteLater()

        requests = read_requests(self._workspace_root)
        self._body.addWidget(_section_title(f"Pending requests ({len(requests)})"))
        if not requests:
            hint = QLabel("No pending requests.")
            hint.setObjectName("DialogHint")
            self._body.addWidget(hint)
        for req in requests:
            self._body.addWidget(self._request_row(req))

        self._body.addWidget(self._sep())
        users = [u for u in read_roster(self._workspace_root)]
        self._body.addWidget(_section_title(f"Users ({sum(1 for u in users if u.active)} active)"))
        for user in users:
            self._body.addWidget(self._user_row(user))
        self._body.addStretch(1)

    def _sep(self) -> QFrame:
        line = QFrame(self)
        line.setObjectName("ItemNotesHRule")
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        return line

    def _avatar_label(self, user_or_initials, color="#3b82f6", img: Path | None = None) -> QLabel:
        lab = QLabel(self)
        lab.setFixedSize(32, 32)
        if isinstance(user_or_initials, StudioUser):
            lab.setPixmap(
                avatar_pixmap_for(
                    avatar_path(self._workspace_root, user_or_initials),
                    user_or_initials.initials,
                    user_or_initials.color_hex,
                    32,
                )
            )
        else:
            lab.setPixmap(avatar_pixmap_for(img, str(user_or_initials), color, 32))
        return lab

    def _request_row(self, req: AccountRequest) -> QWidget:
        w = QFrame(self)
        w.setObjectName("IdentityUserRow")
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setMinimumWidth(520)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        initials = (req.name.strip()[:2].upper() or "?")
        img = None
        if req.avatar:
            from monostudio.core.user_identity import avatars_dir

            cand = avatars_dir(self._workspace_root) / req.avatar
            img = cand if cand.is_file() else None
        top.addWidget(self._avatar_label(initials, "#f59e0b", img), 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(req.name, w)
        name.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        name.setStyleSheet("color: #fafafa; background: transparent;")
        name.setWordWrap(True)
        col.addWidget(name)
        meta = QLabel(req.email or req.requested_at[:10] or "—", w)
        meta.setObjectName("DialogHint")
        col.addWidget(meta)
        top.addLayout(col, 1)
        role_combo = _make_role_combo("artist", w)
        top.addWidget(role_combo, 0, Qt.AlignVCenter)
        outer.addLayout(top)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        approve = _team_action_button("Approve", w, primary=True)
        approve.clicked.connect(
            lambda _=False, rid=req.id, cb=role_combo: self._approve(
                rid, str(cb.currentData() or "artist")
            )
        )
        reject = _team_action_button("Reject", w)
        reject.clicked.connect(lambda _=False, rid=req.id: self._reject(rid))
        actions.addWidget(approve, 0)
        actions.addWidget(reject, 0)
        outer.addLayout(actions)
        return w

    def _user_row(self, user: StudioUser) -> QWidget:
        w = QFrame(self)
        w.setObjectName("IdentityUserRow")
        w.setAttribute(Qt.WA_StyledBackground, True)
        w.setMinimumWidth(520)
        outer = QVBoxLayout(w)
        outer.setContentsMargins(10, 8, 10, 8)
        outer.setSpacing(8)

        top = QHBoxLayout()
        top.setSpacing(10)
        top.addWidget(self._avatar_label(user), 0, Qt.AlignVCenter)
        col = QVBoxLayout()
        col.setSpacing(1)
        name = QLabel(user.name + ("" if user.active else "  (inactive)"), w)
        name.setFont(monos_font("Inter", 13, QFont.Weight.DemiBold))
        name.setStyleSheet(f"color: {'#fafafa' if user.active else '#71717a'}; background: transparent;")
        name.setWordWrap(True)
        col.addWidget(name)
        top.addLayout(col, 1)
        role_combo = _make_role_combo(user.role, w)
        self._wire_role_combo(role_combo, user.id)
        top.addWidget(role_combo, 0, Qt.AlignVCenter)
        outer.addLayout(top)

        actions = QHBoxLayout()
        actions.setSpacing(8)
        actions.addStretch(1)
        photo = _team_action_button("Photo", w)
        photo.clicked.connect(lambda _=False, uid=user.id: self._set_photo(uid))
        actions.addWidget(photo, 0)
        reset = _team_action_button("Reset pw", w)
        reset.clicked.connect(lambda _=False, uid=user.id: self._reset_pw(uid))
        actions.addWidget(reset, 0)
        if user.active:
            deact = _team_action_button("Disable", w)
            deact.setProperty("class", "danger-action")
            deact.clicked.connect(lambda _=False, uid=user.id: self._deactivate(uid))
            actions.addWidget(deact, 0)
        else:
            react = _team_action_button("Enable", w)
            react.clicked.connect(lambda _=False, uid=user.id: self._reactivate(uid))
            actions.addWidget(react, 0)
        delete = _team_action_button("Delete", w)
        delete.setProperty("class", "danger-action")
        delete.clicked.connect(lambda _=False, u=user: self._delete_user(u))
        actions.addWidget(delete, 0)
        outer.addLayout(actions)
        return w

    def _wire_role_combo(self, combo: QComboBox, user_id: str) -> None:
        def on_change(_index: int) -> None:
            role = combo.currentData()
            if not role:
                return
            set_user_role(self._workspace_root, user_id, str(role))

        combo.currentIndexChanged.connect(on_change)

    # --- actions ------------------------------------------------------------
    def _approve(self, req_id: str, role: str) -> None:
        approve_request(self._workspace_root, req_id, role=role)
        self._rebuild()

    def _reject(self, req_id: str) -> None:
        reject_request(self._workspace_root, req_id)
        self._rebuild()

    def _deactivate(self, user_id: str) -> None:
        deactivate_user(self._workspace_root, user_id)
        self._rebuild()

    def _delete_user(self, user: StudioUser) -> None:
        cur = get_current_user(self._workspace_root)
        if cur is not None and cur.id == user.id:
            QMessageBox.warning(
                self,
                "Delete user",
                "You are signed in as this user. Switch to another account or log out first.",
            )
            return
        state = "active" if user.active else "inactive"
        if not ask_delete(
            self,
            "Delete user",
            f"Permanently remove {user.name} ({user.id}) from the studio roster?\n\n"
            f"This user is currently {state}. Notes and history may still show their name; "
            f"this cannot be undone.",
        ):
            return
        if not delete_user(self._workspace_root, user.id):
            QMessageBox.warning(
                self,
                "Delete user",
                "Could not update the shared roster (file may be locked by Dropbox). Try again.",
            )
            return
        self._rebuild()

    def _reactivate(self, user_id: str) -> None:
        for u in read_roster(self._workspace_root):
            if u.id == user_id and not u.active:
                upsert_user(self._workspace_root, _replace_user(u, active=True))
                break
        self._rebuild()

    def _reset_pw(self, user_id: str) -> None:
        pw, ok = QInputDialog.getText(
            self, "Reset password", "New password:", QLineEdit.Password
        )
        if ok and pw:
            set_password(self._workspace_root, user_id, pw)
            self._rebuild()

    def _set_photo(self, user_id: str) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose profile photo", "", "Images (*.png *.jpg *.jpeg *.webp *.bmp)"
        )
        if not path:
            return
        try:
            filename = save_avatar_image(self._workspace_root, user_id, Path(path))
        except (OSError, ValueError):
            return
        set_user_avatar(self._workspace_root, user_id, filename)
        self._rebuild()

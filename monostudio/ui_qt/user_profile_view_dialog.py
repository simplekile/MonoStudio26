"""Read-only studio user profile (e.g. from @mention in notes)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QUrl, QSize
from PySide6.QtGui import QDesktopServices, QFont, QGuiApplication
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
)

from monostudio.core.user_identity import (
    StudioUser,
    avatar_path,
    get_user,
    studio_role_label,
)
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.style import MonosDialog, monos_font, monos_modal_parent
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio


def _format_departments(user: StudioUser) -> str:
    if not user.departments:
        return ""
    return ", ".join(d.replace("_", " ").title() for d in user.departments)


class _ProfileCircleButton(QPushButton):
    def __init__(
        self,
        *,
        icon_name: str,
        tooltip: str,
        parent=None,
        enabled: bool = True,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("UserProfileActionBtn")
        self.setToolTip(tooltip)
        self.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.setIcon(lucide_icon(icon_name, size=18, color_hex="#fafafa"))
        self.setIconSize(QSize(18, 18))
        self.setFixedSize(40, 40)
        self.setEnabled(enabled)


class _ProfileActionDivider(QFrame):
    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("UserProfileActionDivider")
        self.setFrameShape(QFrame.Shape.VLine)
        self.setFixedWidth(1)
        self.setFixedHeight(28)


class UserProfileViewDialog(MonosDialog):
    def __init__(self, *, workspace_root: Path, user: StudioUser, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("UserProfileViewDialog")
        self.setWindowTitle(user.name or "Profile")
        self.setModal(True)
        self.setFixedWidth(320)

        email = (user.email or "").strip()
        dept_text = _format_departments(user)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 32, 24, 24)
        layout.setSpacing(0)
        layout.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        avatar_lbl = QLabel(self)
        avatar_lbl.setObjectName("UserProfileViewAvatar")
        avatar_size = 96
        avatar_lbl.setFixedSize(avatar_size, avatar_size)
        avatar_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        dpr = effective_device_pixel_ratio(self)
        avatar_lbl.setPixmap(
            avatar_pixmap_for(
                avatar_path(workspace_root, user),
                user.initials,
                user.color_hex,
                avatar_size,
                dpr=dpr,
            )
        )
        layout.addWidget(avatar_lbl, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(16)

        name = QLabel(user.name, self)
        name.setObjectName("UserProfileViewName")
        name.setFont(monos_font("Inter", 18, QFont.Weight.DemiBold))
        name.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(name)

        subtitle_parts = [studio_role_label(user.role)]
        if dept_text:
            subtitle_parts.append(dept_text)
        subtitle = QLabel(" · ".join(subtitle_parts), self)
        subtitle.setObjectName("UserProfileViewSubtitle")
        subtitle.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        subtitle.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        if not user.active:
            layout.addSpacing(6)
            inactive = QLabel("Inactive", self)
            inactive.setObjectName("DialogWarning")
            inactive.setFont(monos_font("Inter", 11, QFont.Weight.DemiBold))
            inactive.setAlignment(Qt.AlignmentFlag.AlignHCenter)
            layout.addWidget(inactive)

        layout.addSpacing(24)

        actions = QHBoxLayout()
        actions.setSpacing(12)
        actions.setAlignment(Qt.AlignmentFlag.AlignHCenter)

        email_btn = _ProfileCircleButton(
            icon_name="send",
            tooltip="Send email" if email else "No email on file",
            parent=self,
            enabled=bool(email),
        )
        email_btn.clicked.connect(lambda: self._open_email(email))
        actions.addWidget(email_btn)

        copy_btn = _ProfileCircleButton(
            icon_name="copy",
            tooltip="Copy email" if email else "No email on file",
            parent=self,
            enabled=bool(email),
        )
        copy_btn.clicked.connect(lambda: self._copy_text(email, "Email copied."))
        actions.addWidget(copy_btn)
        actions.addWidget(_ProfileActionDivider(self))

        dept_btn = _ProfileCircleButton(
            icon_name="layers",
            tooltip=dept_text if dept_text else "No departments assigned",
            parent=self,
            enabled=bool(dept_text),
        )
        dept_btn.clicked.connect(lambda: self._copy_text(dept_text, "Departments copied."))
        actions.addWidget(dept_btn)

        user_btn = _ProfileCircleButton(
            icon_name="user",
            tooltip="Copy display name",
            parent=self,
        )
        user_btn.clicked.connect(lambda: self._copy_text(user.name, "Name copied."))
        actions.addWidget(user_btn)

        layout.addLayout(actions)

        layout.addSpacing(20)
        close_row = QHBoxLayout()
        close_row.setAlignment(Qt.AlignmentFlag.AlignHCenter)
        close_btn = QPushButton("Close", self)
        close_btn.setObjectName("UserProfileCloseBtn")
        close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        close_btn.setMinimumWidth(120)
        close_btn.clicked.connect(self.accept)
        close_row.addWidget(close_btn)
        layout.addLayout(close_row)

    def _open_email(self, email: str) -> None:
        addr = (email or "").strip()
        if not addr:
            return
        QDesktopServices.openUrl(QUrl(f"mailto:{addr}"))

    def _copy_text(self, text: str, message: str) -> None:
        value = (text or "").strip()
        if not value:
            return
        QGuiApplication.clipboard().setText(value)
        from monostudio.ui_qt.notification import notify as notification_service

        notification_service.info(message)


def open_studio_user_profile(
    workspace_root: Path | None,
    user_id: str,
    *,
    parent=None,
) -> None:
    """Open read-only roster profile (notes @mention, schedule history, …)."""
    from monostudio.ui_qt.notification import notify as notification_service

    uid = (user_id or "").strip()
    if not uid:
        return
    if workspace_root is None:
        notification_service.info("Select a workspace to view profiles.")
        return

    user = get_user(workspace_root, uid)
    if user is None:
        notification_service.warning("That user is no longer on the team roster.")
        return

    dlg = UserProfileViewDialog(
        workspace_root=workspace_root,
        user=user,
        parent=monos_modal_parent(parent),
    )
    dlg.exec()

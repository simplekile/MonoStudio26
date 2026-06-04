"""Facebook-style notification row (avatar, body, time, read-state icon)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from monostudio.core.mention_inbox import read_inbox
from monostudio.core.user_identity import avatar_path, get_user, read_roster
from monostudio.ui_qt.lucide_icons import lucide_icon
from monostudio.ui_qt.notification.mention_alert_format import (
    _fields_from_entry,
    apply_notification_message_label,
)
from monostudio.ui_qt.notification.store import NotificationEntry, UserAlertPayload
from monostudio.ui_qt.notification.toast import TOAST_ICONS
from monostudio.ui_qt.style import MONOS_COLORS, monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio


def _resolve_workspace_root(widget: QWidget) -> Path | None:
    win = widget.window()
    if win is None:
        return None
    root = getattr(win, "_workspace_root", None)
    return Path(root) if root else None


def _parse_iso_datetime(raw: str) -> datetime | None:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except ValueError:
        return None


def _payload(entry: NotificationEntry) -> UserAlertPayload:
    p = entry.payload
    if isinstance(p, dict):
        return UserAlertPayload.from_dict(p)
    return p


def _resolve_from_user_id(
    entry: NotificationEntry,
    workspace_root: Path | None,
    project_root: Path | None,
) -> str:
    p = _payload(entry)
    uid = (p.from_user_id or "").strip()
    if uid:
        return uid
    mid = (p.mention_inbox_id or "").strip()
    if mid and project_root is not None:
        try:
            for item in read_inbox(project_root):
                if item.id == mid and (item.from_user_id or "").strip():
                    return item.from_user_id.strip()
        except OSError:
            pass
    name = (p.from_name or "").strip()
    if name and workspace_root is not None:
        try:
            for u in read_roster(workspace_root):
                if (u.name or "").strip() == name and (u.id or "").strip():
                    return u.id.strip()
        except OSError:
            pass
    return ""


def display_time_for_entry(
    entry: NotificationEntry,
    project_root: Path | None,
) -> datetime:
    """Prefer mention inbox timestamp over bell-append time."""
    p = _payload(entry)
    mid = (p.mention_inbox_id or "").strip()
    if mid and project_root is not None:
        try:
            for item in read_inbox(project_root):
                if item.id == mid and item.at:
                    parsed = _parse_iso_datetime(item.at)
                    if parsed is not None:
                        return parsed
        except OSError:
            pass
    at = entry.at
    if at.tzinfo is None:
        return at.replace(tzinfo=timezone.utc)
    return at


def format_notification_time(dt: datetime) -> str:
    """Short relative time (English, local clock)."""
    try:
        now = datetime.now().astimezone()
        if dt.tzinfo is None:
            at = dt.replace(tzinfo=timezone.utc).astimezone()
        else:
            at = dt.astimezone()
        secs = max(0, int((now - at).total_seconds()))
    except (TypeError, ValueError, OSError):
        return "—"
    if secs < 45:
        return "Just now"
    mins = secs // 60
    if mins < 60:
        return "1m" if mins == 1 else f"{mins}m"
    hours = mins // 60
    if hours < 24:
        return "1h" if hours == 1 else f"{hours}h"
    days = hours // 24
    if days < 7:
        return "1d" if days == 1 else f"{days}d"
    weeks = days // 7
    if weeks < 5:
        return "1w" if weeks == 1 else f"{weeks}w"
    months = max(1, days // 30)
    if months < 12:
        return "1mo" if months == 1 else f"{months}mo"
    years = max(1, days // 365)
    return "1y" if years == 1 else f"{years}y"


class _ReadStateIcon(QLabel):
    """Unread = blue circle; read = muted circle-check."""

    _ICON_PX = 16

    def __init__(self, *, read: bool, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("NotificationReadStateIcon")
        self.setFixedSize(20, 20)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        if read:
            name = "circle-check"
            color = MONOS_COLORS.get("text_meta", "#71717a")
            self.setToolTip("Read")
        else:
            name = "circle"
            color = "#3b82f6"
            self.setToolTip("Unread")
        icon = lucide_icon(name, size=self._ICON_PX, color_hex=color)
        self.setPixmap(icon.pixmap(self._ICON_PX, self._ICON_PX))


class _AvatarBadge(QWidget):
    """Circular avatar with small action badge (bottom-right)."""

    def __init__(
        self,
        *,
        image_path: Path | None,
        initials: str,
        color_hex: str,
        badge_icon_name: str = "message-circle",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setFixedSize(56, 56)
        dpr = effective_device_pixel_ratio(self)
        self._avatar = QLabel(self)
        self._avatar.setFixedSize(48, 48)
        self._avatar.setPixmap(
            avatar_pixmap_for(image_path, initials, color_hex, 48, dpr=dpr)
        )
        self._avatar.move(0, 4)

        self._badge = QLabel(self)
        self._badge.setFixedSize(22, 22)
        self._badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge_pix = lucide_icon(badge_icon_name, size=12, color_hex="#fafafa").pixmap(12, 12)
        self._badge.setPixmap(badge_pix)
        self._badge.setStyleSheet(
            "background-color: #3b82f6; border: 2px solid #1c1c1f; border-radius: 11px;"
        )
        self._badge.move(34, 30)


def _sender_visual(
    entry: NotificationEntry,
    workspace_root: Path | None,
    project_root: Path | None = None,
) -> tuple[Path | None, str, str, str]:
    """image_path, initials, color_hex, badge_icon."""
    p = _payload(entry)
    from_uid = _resolve_from_user_id(entry, workspace_root, project_root)
    from_name = (p.from_name or "").strip()
    fields = _fields_from_entry(entry)
    if fields:
        from_name = from_name or fields[0]
    if from_uid and workspace_root is not None:
        user = get_user(workspace_root, from_uid)
        if user is not None:
            return (
                avatar_path(workspace_root, user),
                user.initials,
                user.color_hex or "#3b82f6",
                "message-circle",
            )
    initials = "?"
    if from_name:
        parts = [x for x in from_name.replace("_", " ").split() if x]
        if len(parts) >= 2:
            initials = (parts[0][0] + parts[-1][0]).upper()
        elif parts:
            initials = parts[0][:2].upper()
    t = entry.toast_type
    icon = TOAST_ICONS.get(t, "message-circle")
    return (None, initials, "#52525b", icon)


class NotificationAlertRow(QFrame):
    """Single notification row — layout similar to Facebook mobile."""

    clicked = Signal(object)

    def __init__(
        self,
        entry: NotificationEntry,
        parent=None,
        *,
        workspace_root: Path | None = None,
        project_root: Path | None = None,
    ) -> None:
        super().__init__(parent)
        self._entry = entry
        self.setObjectName("NotificationAlertRow")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        unread = not entry.read
        if unread:
            self.setProperty("unread", "true")
        else:
            self.setProperty("unread", "false")
        self.setStyleSheet(
            """
            QFrame#NotificationAlertRow {
                background: transparent;
                border: none;
                border-radius: 8px;
            }
            QFrame#NotificationAlertRow[unread="true"] {
                background: rgba(59, 130, 246, 0.08);
            }
            QFrame#NotificationAlertRow:hover {
                background: rgba(255, 255, 255, 0.05);
            }
            QFrame#NotificationAlertRow[unread="true"]:hover {
                background: rgba(59, 130, 246, 0.12);
            }
            QLabel#NotificationReadStateIcon {
                background: transparent;
                border: none;
            }
            """
        )

        ws = workspace_root if workspace_root is not None else _resolve_workspace_root(self)
        pr = project_root
        img, initials, color, badge_icon = _sender_visual(entry, ws, pr)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 8, 10)
        root.setSpacing(10)

        root.addWidget(
            _AvatarBadge(
                image_path=img,
                initials=initials,
                color_hex=color,
                badge_icon_name=badge_icon,
                parent=self,
            ),
            0,
            Qt.AlignmentFlag.AlignTop,
        )

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(4)

        msg = QLabel(self)
        apply_notification_message_label(msg, entry)
        msg.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        text_col.addWidget(msg)

        time_l = QLabel(format_notification_time(display_time_for_entry(entry, pr)), self)
        time_l.setFont(monos_font("Inter", 12, QFont.Weight.Normal))
        time_color = "#60a5fa" if unread else MONOS_COLORS.get("text_meta", "#71717a")
        time_l.setStyleSheet(f"color: {time_color}; background: transparent; border: none;")
        text_col.addWidget(time_l, 0, Qt.AlignmentFlag.AlignLeft)

        root.addLayout(text_col, 1)

        root.addWidget(
            _ReadStateIcon(read=entry.read, parent=self),
            0,
            Qt.AlignmentFlag.AlignVCenter,
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self._entry)
            event.accept()
            return
        super().mousePressEvent(event)

    def sizeHint(self):  # type: ignore[override]
        from PySide6.QtCore import QSize

        return QSize(340, 76)

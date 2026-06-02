"""
MonoStudio26 notification system: non-blocking toasts via a single service.
Use only NotificationService (notify) from UI and logic; do not create toast widgets directly.
"""

from monostudio.ui_qt.notification.service import notify, _NotificationService
from monostudio.ui_qt.notification.store import (
    NotificationEntry,
    UserAlertPayload,
    recent as notification_recent,
    all_entries as notification_all_entries,
    count as notification_count,
    unread_count as notification_unread_count,
    mark_read as notification_mark_read,
    mark_all_read as notification_mark_all_read,
)

__all__ = [
    "notify",
    "NotificationService",
    "NotificationEntry",
    "UserAlertPayload",
    "notification_recent",
    "notification_all_entries",
    "notification_count",
    "notification_unread_count",
    "notification_mark_read",
    "notification_mark_all_read",
]

# Alias for discoverability
NotificationService = _NotificationService

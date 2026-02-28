"""
MonoStudio26 notification system: non-blocking toasts via a single service.
Use only NotificationService (notify) from UI and logic; do not create toast widgets directly.
"""

from monostudio.ui_qt.notification.service import notify, _NotificationService
from monostudio.ui_qt.notification.store import (
    NotificationEntry,
    recent as notification_recent,
    all_entries as notification_all_entries,
    count as notification_count,
)

__all__ = [
    "notify",
    "NotificationService",
    "NotificationEntry",
    "notification_recent",
    "notification_all_entries",
    "notification_count",
]

# Alias for discoverability
NotificationService = _NotificationService

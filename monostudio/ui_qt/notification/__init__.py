"""
MonoStudio26 notification system: non-blocking toasts via a single service.
Use only NotificationService (notify) from UI and logic; do not create toast widgets directly.
"""

from monostudio.ui_qt.notification.service import notify, _NotificationService

__all__ = ["notify", "NotificationService"]

# Alias for discoverability
NotificationService = _NotificationService

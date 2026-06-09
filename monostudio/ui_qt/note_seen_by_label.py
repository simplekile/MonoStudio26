"""Read-receipt row for note cards and viewers (avatar stack)."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QMouseEvent
from PySide6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QWidget

from monostudio.core.item_comments import ItemCommentEntry, NoteAuthorVisual, seen_by_visual
from monostudio.ui_qt.style import monos_font
from monostudio.ui_qt.user_avatar import avatar_pixmap_for, effective_device_pixel_ratio

_AVATAR_PX = 20
_PREVIEW_MAX = 10
_OVERLAP_PX = 6


class _SeenByAvatar(QLabel):
    """Small circular avatar; tooltip = full name, click opens studio profile."""

    def __init__(
        self,
        visual: NoteAuthorVisual,
        *,
        workspace_root: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NoteSeenByAvatar")
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._user_id = (visual.user_id or "").strip()
        self.setFixedSize(_AVATAR_PX, _AVATAR_PX)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setScaledContents(False)
        self.setToolTip(visual.name)
        dpr = effective_device_pixel_ratio(self)
        self.setPixmap(
            avatar_pixmap_for(
                visual.image_path,
                visual.initials,
                visual.color_hex,
                _AVATAR_PX,
                dpr=dpr,
            )
        )
        if self._user_id:
            self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if (
            self._user_id
            and event.button() == Qt.MouseButton.LeftButton
            and self._workspace_root is not None
        ):
            from monostudio.ui_qt.user_profile_view_dialog import open_studio_user_profile

            open_studio_user_profile(self._workspace_root, self._user_id, parent=self.window())
            event.accept()
            return
        super().mousePressEvent(event)


class NoteSeenByRow(QWidget):
    """``Seen by`` prefix + overlapping avatar chips."""

    def __init__(
        self,
        entry: ItemCommentEntry,
        workspace_root: Path | None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("NoteSeenByRow")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

        viewers = [s for s in entry.seen_by if (s.user_id or "").strip()]
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 2, 0, 0)
        layout.setSpacing(8)

        prefix = QLabel("Seen by", self)
        prefix.setObjectName("NoteSeenByLabel")
        prefix.setFont(monos_font("Inter", 11, QFont.Weight.Medium))
        layout.addWidget(prefix, 0, Qt.AlignmentFlag.AlignVCenter)

        stack_host = QWidget(self)
        stack_host.setObjectName("NoteSeenByAvatarStack")
        stack_l = QHBoxLayout(stack_host)
        stack_l.setContentsMargins(0, 0, 2, 2)
        stack_l.setSpacing(-_OVERLAP_PX)
        stack_l.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        shown = viewers[:_PREVIEW_MAX]
        extra = len(viewers) - len(shown)
        for seen in shown:
            visual = seen_by_visual(seen, workspace_root)
            stack_l.addWidget(
                _SeenByAvatar(visual, workspace_root=workspace_root, parent=stack_host)
            )

        if extra > 0:
            more = QLabel(f"+{extra}", stack_host)
            more.setObjectName("NoteSeenByMore")
            more.setAlignment(Qt.AlignmentFlag.AlignCenter)
            more.setFixedSize(_AVATAR_PX, _AVATAR_PX)
            more.setFont(monos_font("Inter", 9, QFont.Weight.DemiBold))
            extra_names = ", ".join(
                seen_by_visual(s, workspace_root).name for s in viewers[_PREVIEW_MAX:]
            )
            more.setToolTip(extra_names)
            stack_l.addWidget(more)

        layout.addWidget(stack_host, 0, Qt.AlignmentFlag.AlignVCenter)
        layout.addStretch(1)


def note_seen_by_label(
    entry: ItemCommentEntry,
    workspace_root: Path | None,
    parent: QWidget,
) -> QWidget | None:
    """Build a read-receipt row, or ``None`` when there are no receipts."""
    if not any((s.user_id or "").strip() for s in entry.seen_by):
        return None
    return NoteSeenByRow(entry, workspace_root, parent)

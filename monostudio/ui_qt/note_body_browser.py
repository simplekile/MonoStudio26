"""Read-only note body with clickable inline images."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import (
    QFont,
    QFontMetrics,
    QMouseEvent,
    QColor,
)
from PySide6.QtWidgets import QLabel, QSizePolicy, QTextBrowser, QWidget


def resolve_note_jump_host(widget: QWidget | None) -> QWidget | None:
    """Find MainWindow (or any widget exposing jump_to_note_time_anchor)."""
    w = widget
    while w is not None:
        if callable(getattr(w, "jump_to_note_time_anchor", None)):
            return w
        w = w.parentWidget()
    return None

from monostudio.core.item_comments import ItemCommentEntry, entry_preview_text, is_mention_note_href, user_id_from_mention_href
from monostudio.core.note_time_anchors import is_time_note_href, parse_time_href, parse_time_href_from_html
from monostudio.ui_qt.note_compose_editor import (
    NOTE_BODY_FONT,
    NOTE_RICH_TEXT_STYLESHEET,
    normalize_note_document_spacing,
    note_html_for_display,
)
from monostudio.ui_qt.note_image_frame import (
    prime_framed_image_resources,
    resolve_note_image_path,
    set_note_image_resource_hover,
)
from monostudio.ui_qt.note_image_hit_test import image_href_at_widget_pos
from monostudio.ui_qt.note_image_viewer_dialog import NoteImageViewerDialog

def _open_note_image(item_root: Path, href: str, *, parent) -> bool:
    p = resolve_note_image_path(item_root, href)
    if p is None:
        return False
    dlg = NoteImageViewerDialog(p, parent=parent)
    dlg.exec()
    return True


class NoteBodyBrowser(QTextBrowser):
    time_anchor_clicked = Signal(str)

    def __init__(self, *, item_root: Path, workspace_root: Path | None = None, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ItemNotesBodyBrowser")
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.setFont(NOTE_BODY_FONT)
        self.setFrameShape(self.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizeAdjustPolicy(QTextBrowser.SizeAdjustPolicy.AdjustToContents)
        self._item_root = Path(item_root)
        self._workspace_root = Path(workspace_root) if workspace_root else None
        mono = self._item_root / ".monostudio"
        self.document().setBaseUrl(QUrl.fromLocalFile(str(mono) + os.sep))
        self.document().setDefaultFont(NOTE_BODY_FONT)
        self.document().setDefaultStyleSheet(NOTE_RICH_TEXT_STYLESHEET)
        self.document().setDocumentMargin(4)
        self.anchorClicked.connect(self._on_anchor)
        self.viewport().setMouseTracking(True)
        self._hover_img_href: str | None = None

    def _thumb_dpr(self) -> float:
        return max(2.0, float(self.devicePixelRatioF()))

    def _clear_image_hover(self) -> None:
        if not self._hover_img_href:
            return
        set_note_image_resource_hover(
            self.document(),
            self._item_root,
            self._hover_img_href,
            hovered=False,
            device_pixel_ratio=self._thumb_dpr(),
        )
        self._hover_img_href = None
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
        self.viewport().update()

    def _handle_mouse_move(self, viewport_pos) -> None:
        href = self.anchorAt(viewport_pos)
        if href and (is_mention_note_href(href) or is_time_note_href(href)):
            self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            return
        self._handle_image_mouse_move(viewport_pos)

    def _handle_image_mouse_move(self, viewport_pos) -> None:
        href = image_href_at_widget_pos(self, viewport_pos)
        if href != self._hover_img_href:
            if self._hover_img_href:
                set_note_image_resource_hover(
                    self.document(),
                    self._item_root,
                    self._hover_img_href,
                    hovered=False,
                    device_pixel_ratio=self._thumb_dpr(),
                )
            self._hover_img_href = href
            if href:
                set_note_image_resource_hover(
                    self.document(),
                    self._item_root,
                    href,
                    hovered=True,
                    device_pixel_ratio=self._thumb_dpr(),
                )
                self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.viewport().setCursor(Qt.CursorShape.ArrowCursor)
            self.viewport().update()

    def _handle_mouse_press(self, viewport_pos) -> bool:
        href = self.anchorAt(viewport_pos)
        if href:
            self._on_anchor(QUrl(href))
            return True
        name = image_href_at_widget_pos(self, viewport_pos)
        if name and _open_note_image(self._item_root, name, parent=self.window()):
            return True
        return False

    def viewportEvent(self, event: QEvent) -> bool:  # type: ignore[override]
        if isinstance(event, QMouseEvent):
            pos = event.position().toPoint()
            et = event.type()
            if et == QEvent.Type.MouseMove:
                self._handle_mouse_move(pos)
            elif et == QEvent.Type.Leave:
                self._clear_image_hover()
            elif et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                if self._handle_mouse_press(pos):
                    event.accept()
                    return True
        return super().viewportEvent(event)

    def set_body(self, html: str, *, plain_fallback: str = "", done: bool = False) -> None:
        body = (html or "").strip()
        if not body:
            body = f"<p>{(plain_fallback or '').replace(chr(10), '<br>')}</p>"
        else:
            body = note_html_for_display(body)
        self._hover_img_href = None
        dpr = self._thumb_dpr()
        prime_framed_image_resources(
            self.document(), self._item_root, body, device_pixel_ratio=dpr
        )
        stylesheet = NOTE_RICH_TEXT_STYLESHEET
        if done:
            stylesheet += " body, p, li, span, a { text-decoration: line-through; }"
        self.document().setDefaultStyleSheet(stylesheet)
        self.setHtml(body)
        normalize_note_document_spacing(self.document())
        color = "#71717a" if done else "#d4d4d8"
        self.setStyleSheet(f"color: {color}; background: transparent;")
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def setSource(self, url: QUrl) -> None:  # type: ignore[override]
        """Block Qt from loading custom mention/time anchors as documents."""
        href = url.toString()
        if is_mention_note_href(href) or is_time_note_href(href):
            return
        super().setSource(url)

    def _on_anchor(self, url: QUrl) -> None:
        href = url.toString()
        if is_time_note_href(href):
            if parse_time_href(href) is not None:
                self.time_anchor_clicked.emit(href)
            return
        if is_mention_note_href(href):
            uid = user_id_from_mention_href(href)
            if uid:
                from monostudio.ui_qt.user_profile_view_dialog import open_studio_user_profile

                open_studio_user_profile(self._workspace_root, uid, parent=self.window())
            return
        if href.startswith("monos-img:") or "note_media" in href:
            _open_note_image(self._item_root, href, parent=self.window())


class NoteListPreviewLabel(QLabel):
    """One-line note preview — plain text elided like schedule milestone labels."""

    open_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ItemNotesPreviewLabel")
        self.setFont(NOTE_BODY_FONT)
        self.setWordWrap(False)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._full_text = ""

    def set_preview(self, text: str, *, done: bool = False) -> None:
        self._full_text = (text or "").replace("\n", " ").strip()
        color = "#71717a" if done else "#d4d4d8"
        self.setStyleSheet(f"color: {color}; background: transparent;")
        f = QFont(NOTE_BODY_FONT)
        f.setStrikeOut(done)
        self.setFont(f)
        fm = QFontMetrics(f)
        self.setMinimumHeight(fm.height() + 2)
        self._apply_elide()

    def _apply_elide(self) -> None:
        if not self._full_text:
            self.setText("")
            self.setToolTip("")
            return
        fm = QFontMetrics(self.font())
        max_w = max(8, self.contentsRect().width())
        elided = fm.elidedText(self._full_text, Qt.TextElideMode.ElideRight, max_w)
        self.setText(elided)
        self.setToolTip(self._full_text if elided != self._full_text else "")

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._apply_elide()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.open_requested.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


def note_has_time_pill_html(html: str) -> bool:
    body = (html or "").strip()
    if not body:
        return False
    if parse_time_href_from_html(body) is not None:
        return True
    return 'href="monos-time:' in body or is_time_note_href(body)


def make_note_card_preview(
    entry: ItemCommentEntry,
    *,
    item_root: Path,
    workspace_root: Path | None,
    parent,
    on_time_anchor: Callable[[str], None] | None = None,
    on_plain_open: Callable[[], None] | None = None,
) -> QWidget:
    """Compact list preview — rich browser when note has timeline pills."""
    html = (entry.body_html or "").strip()
    if note_has_time_pill_html(html):
        preview = NoteBodyBrowser(
            item_root=item_root,
            workspace_root=workspace_root,
            parent=parent,
        )
        preview.setMaximumHeight(52)
        preview.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        preview.set_body(html, plain_fallback=entry_preview_text(entry), done=entry.done)
        if on_time_anchor is not None:
            preview.time_anchor_clicked.connect(on_time_anchor)
        return preview
    plain = NoteListPreviewLabel(parent)
    plain.set_preview(entry_preview_text(entry), done=entry.done)
    if on_plain_open is not None:
        plain.open_requested.connect(on_plain_open)
    return plain

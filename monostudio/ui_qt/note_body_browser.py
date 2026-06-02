"""Read-only note body with clickable inline images."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import QEvent, Qt, QUrl, Signal
from PySide6.QtGui import QFont, QMouseEvent, QTextDocument
from PySide6.QtWidgets import QTextBrowser

from monostudio.ui_qt.note_compose_editor import (
    NOTE_LINE_MIN_H,
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
from monostudio.ui_qt.style import monos_font


def _open_note_image(item_root: Path, href: str, *, parent) -> bool:
    p = resolve_note_image_path(item_root, href)
    if p is None:
        return False
    dlg = NoteImageViewerDialog(p, parent=parent)
    dlg.exec()
    return True


class NoteBodyBrowser(QTextBrowser):
    def __init__(self, *, item_root: Path, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("ItemNotesBodyBrowser")
        self.setOpenExternalLinks(False)
        self.setFont(monos_font("Inter", 13, QFont.Weight.Normal))
        self.setFrameShape(self.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizeAdjustPolicy(QTextBrowser.SizeAdjustPolicy.AdjustToContents)
        self._item_root = Path(item_root)
        mono = self._item_root / ".monostudio"
        self.document().setBaseUrl(QUrl.fromLocalFile(str(mono) + os.sep))
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
        self.setHtml(body)
        normalize_note_document_spacing(self.document())
        color = "#71717a" if done else "#d4d4d8"
        self.setStyleSheet(f"color: {color}; background: transparent;")
        self.viewport().setCursor(Qt.CursorShape.ArrowCursor)

    def _on_anchor(self, url: QUrl) -> None:
        href = url.toString()
        if href.startswith("mention:") or href.startswith("#mention-"):
            return
        if href.startswith("monos-img:") or "note_media" in href:
            _open_note_image(self._item_root, href, parent=self.window())


class NoteListPreviewBrowser(NoteBodyBrowser):
    """Compact rich preview for note list cards — one line, no scroll."""

    open_requested = Signal()

    _PREVIEW_ONE_LINE_H = NOTE_LINE_MIN_H

    def __init__(self, *, item_root: Path, parent=None) -> None:
        super().__init__(item_root=item_root, parent=parent)
        self.setObjectName("ItemNotesPreviewBrowser")
        self.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._content_truncated = False
        self.viewport().setCursor(Qt.CursorShape.PointingHandCursor)

    def is_content_truncated(self) -> bool:
        return self._content_truncated

    @staticmethod
    def _exceeds_one_line(doc: QTextDocument) -> bool:
        if doc.size().height() > NoteListPreviewBrowser._PREVIEW_ONE_LINE_H + 1.0:
            return True
        non_empty_blocks = 0
        block = doc.firstBlock()
        while block.isValid():
            has_content = bool(block.text().strip())
            if not has_content:
                it = block.begin()
                while not it.atEnd():
                    if it.fragment().charFormat().isImageFormat():
                        has_content = True
                        break
                    it += 1
            if has_content:
                non_empty_blocks += 1
                if non_empty_blocks > 1:
                    return True
            block = block.next()
        return False

    def set_body(self, html: str, *, plain_fallback: str = "", done: bool = False) -> None:
        super().set_body(html, plain_fallback=plain_fallback, done=done)
        doc_h = int(self.document().size().height()) + 2
        self._content_truncated = self._exceeds_one_line(self.document())
        view_h = min(doc_h, self._PREVIEW_ONE_LINE_H)
        self.setFixedHeight(max(view_h, 20))

    def _handle_mouse_press(self, viewport_pos) -> bool:
        name = image_href_at_widget_pos(self, viewport_pos)
        if name:
            return _open_note_image(self._item_root, name, parent=self.window())
        return False

    def viewportEvent(self, event: QEvent) -> bool:  # type: ignore[override]
        if isinstance(event, QMouseEvent):
            pos = event.position().toPoint()
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self._handle_mouse_press(pos):
                    event.accept()
                    return True
                self.open_requested.emit()
                event.accept()
                return True
        return super().viewportEvent(event)

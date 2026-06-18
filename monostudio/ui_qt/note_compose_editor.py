"""Rich-text compose editor for notes: paste images + @mentions."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QFont,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QTextBlockFormat,
    QTextCharFormat,
    QTextCursor,
    QTextDocument,
    QTextImageFormat,
)
from PySide6.QtWidgets import QApplication, QSizePolicy, QTextEdit

from monostudio.ui_qt.note_image_frame import (
    NOTE_IMG_BOX_SIZE,
    NOTE_IMG_RADIUS,
    resolve_note_image_path,
    set_note_image_resource_hover,
    square_framed_note_image,
)

from monostudio.core.item_comments import (
    NOTE_MEDIA_DIR,
    mention_href_for_user,
    note_media_entry_dir,
    parse_mentions_from_html,
    strip_html_preview,
)
from monostudio.core.note_time_anchors import (
    NoteTimeKind,
    format_frame_pill_label,
    is_playhead_time_href,
    is_time_note_href,
    time_href_for_playhead,
)
from monostudio.core.user_identity import StudioUser, read_roster
from monostudio.ui_qt.note_mention_popup import NoteMentionPopup
from monostudio.ui_qt.note_image_hit_test import image_href_at_widget_pos
from monostudio.ui_qt.note_image_viewer_dialog import NoteImageViewerDialog
from monostudio.ui_qt.popup_position import position_child_popup_near_global_point
from monostudio.ui_qt.style import monos_font

_FULL_IMG_MAX_PX = 1920
NOTE_BODY_FONT_SIZE = 11
NOTE_BODY_FONT = monos_font("Inter", NOTE_BODY_FONT_SIZE, QFont.Weight.Normal)
NOTE_BODY_COLOR = "#e4e4e7"
_LH_MINIMUM = QTextBlockFormat.LineHeightTypes.MinimumHeight.value
NOTE_RICH_TEXT_STYLESHEET = (
    f"body, p, li, a, span {{ font-family: Inter; font-size: {NOTE_BODY_FONT_SIZE}pt; }}"
    f"body, p, li {{ margin-top: 0px; margin-bottom: 6px; line-height: 1.5; color: {NOTE_BODY_COLOR}; }}"
    "a { text-decoration: none; }"
    "a[href^=\"monos-time:range\"] {"
    "  font-family: \"JetBrains Mono\";"
    "  color: #93c5fd; background-color: rgba(37, 99, 235, 0.28);"
    "  border-radius: 4px; padding: 1px 5px;"
    "}"
    "a[href^=\"monos-time:marker\"] {"
    "  font-family: \"JetBrains Mono\";"
    "  color: #f9a8d4; background-color: rgba(244, 114, 182, 0.22);"
    "  border-radius: 4px; padding: 1px 5px;"
    "}"
    "a[href^=\"monos-time:frame\"] {"
    "  font-family: \"JetBrains Mono\";"
    "  color: #d4d4d8; background-color: rgba(63, 63, 70, 0.55);"
    "  border-radius: 4px; padding: 1px 5px;"
    "}"
    "a[href^=\"monos-time:playhead\"] {"
    "  font-family: \"JetBrains Mono\";"
    "  color: #fde68a; background-color: rgba(245, 158, 11, 0.24);"
    "  border-radius: 4px; padding: 1px 5px;"
    "}"
    "img { vertical-align: top; }"
)


def normalize_note_document_spacing(doc: QTextDocument) -> None:
    """Image rows: fixed line height. Text rows: rely on stylesheet (no block margins)."""
    block = doc.firstBlock()
    while block.isValid():
        has_image = False
        it = block.begin()
        while not it.atEnd():
            frag = it.fragment()
            if frag.isValid() and frag.charFormat().isImageFormat():
                has_image = True
                break
            it += 1
        cursor = QTextCursor(block)
        bf = QTextBlockFormat(block.blockFormat())
        changed = False
        if has_image:
            min_h = float(NOTE_IMG_BOX_SIZE)
            if bf.lineHeightType() != _LH_MINIMUM or bf.lineHeight() < min_h - 0.01:
                bf.setLineHeight(min_h, _LH_MINIMUM)
                changed = True
        else:
            if bf.lineHeightType() == _LH_MINIMUM:
                bf.setLineHeight(150, QTextBlockFormat.LineHeightTypes.ProportionalHeight)
                changed = True
        if bf.bottomMargin() > 0 or bf.topMargin() > 0:
            bf.setTopMargin(0)
            bf.setBottomMargin(0)
            changed = True
        if changed:
            cursor.setBlockFormat(bf)
        block = block.next()


def note_html_for_display(html: str, *, box_size: int = NOTE_IMG_BOX_SIZE) -> str:
    """Clamp inline image size in stored Qt HTML for compact square card/list display."""

    def _fix_img(match: re.Match[str]) -> str:
        tag = match.group(0)
        tag = re.sub(r'\s+width="[^"]*"', "", tag, flags=re.IGNORECASE)
        tag = re.sub(r"\s+width='[^']*'", "", tag, flags=re.IGNORECASE)
        tag = re.sub(r'\s+height="[^"]*"', "", tag, flags=re.IGNORECASE)
        tag = re.sub(r"\s+height='[^']*'", "", tag, flags=re.IGNORECASE)
        style_extra = (
            f"max-width:{box_size}px; width:{box_size}px; "
            f"max-height:{box_size}px; height:{box_size}px; "
            f"border-radius:{NOTE_IMG_RADIUS}px; vertical-align:top; cursor:pointer;"
        )
        sm = re.search(r'style="([^"]*)"', tag, re.IGNORECASE)
        if sm:
            inner = sm.group(1).strip().rstrip(";") + ";" + style_extra
            tag = tag[: sm.start(1)] + inner + tag[sm.end(1) :]
        else:
            tag = tag.replace("<img", f'<img style="{style_extra}"', 1)
        if not re.search(r"\bwidth=", tag, re.IGNORECASE):
            tag = tag.replace("<img", f'<img width="{box_size}"', 1)
        if not re.search(r"\bheight=", tag, re.IGNORECASE):
            tag = tag.replace("<img", f'<img height="{box_size}"', 1)
        return tag

    return re.sub(r"<img\b[^>]*>", _fix_img, html or "", flags=re.IGNORECASE)


def _scale_image(img: QImage, max_px: int) -> QImage:
    w, h = img.width(), img.height()
    if w <= 0 or h <= 0:
        return img
    scale = min(1.0, max_px / max(w, h))
    if scale >= 1.0:
        return img
    nw = max(1, int(round(w * scale)))
    nh = max(1, int(round(h * scale)))
    return img.scaled(nw, nh, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)


class NoteComposeEditor(QTextEdit):
    time_anchor_clicked = Signal(str)

    def __init__(
        self,
        *,
        item_root: Path,
        workspace_root: Path | None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("ItemNotesAddEditor")
        self.setFont(NOTE_BODY_FONT)
        self.setAcceptRichText(True)
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setPlaceholderText("Write a note… Paste images, type @ to mention")
        self._item_root = Path(item_root)
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._entry_id = uuid.uuid4().hex[:16]
        self._monostudio_dir = self._item_root / ".monostudio"
        self._media_dir = note_media_entry_dir(self._item_root, self._entry_id)
        self._mention_popup = NoteMentionPopup(
            self,
            workspace_root=self._workspace_root,
            compose_editor=self,
        )
        self._sync_mention_popup_host()
        self._mention_popup.hide()
        self._mention_popup.user_selected.connect(self._insert_mention)
        self._mention_popup.set_users(read_roster(self._workspace_root))
        self._mention_start = -1
        self._mention_key_capture_host: QObject | None = None
        self._playhead_pill_locked = False
        self.installEventFilter(self)
        self.document().setBaseUrl(QUrl.fromLocalFile(str(self._monostudio_dir) + os.sep))
        self.document().setDefaultFont(NOTE_BODY_FONT)
        self.document().setDefaultStyleSheet(NOTE_RICH_TEXT_STYLESHEET)
        self.document().setDocumentMargin(4)
        self._apply_default_cursor_format()
        self.viewport().setMouseTracking(True)
        self._hover_img_href: str | None = None

    def set_item_root(self, item_root: Path) -> None:
        self._item_root = Path(item_root)
        self._monostudio_dir = self._item_root / ".monostudio"
        self.document().setBaseUrl(QUrl.fromLocalFile(str(self._monostudio_dir) + os.sep))
        self.reset_draft()

    def set_workspace_root(self, workspace_root: Path | None) -> None:
        self._workspace_root = Path(workspace_root) if workspace_root else None
        self._mention_popup.set_users(read_roster(self._workspace_root))

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
        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
        self.viewport().update()

    def viewportEvent(self, event: QEvent) -> bool:  # type: ignore[override]
        if isinstance(event, QMouseEvent):
            pos = event.position().toPoint()
            et = event.type()
            if et == QEvent.Type.MouseMove:
                href = image_href_at_widget_pos(self, pos)
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
                        self.viewport().setCursor(Qt.CursorShape.IBeamCursor)
                    self.viewport().update()
            elif et == QEvent.Type.Leave:
                self._clear_image_hover()
            elif et == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                name = image_href_at_widget_pos(self, pos)
                if name:
                    p = resolve_note_image_path(self._item_root, name)
                    if p is not None:
                        dlg = NoteImageViewerDialog(p, parent=self.window())
                        dlg.exec()
                        event.accept()
                        return True
        return super().viewportEvent(event)

    def showEvent(self, event) -> None:  # type: ignore[override]
        self._sync_mention_popup_host()
        super().showEvent(event)

    def closeEvent(self, event) -> None:  # type: ignore[override]
        self._ensure_mention_key_capture(False)
        super().closeEvent(event)

    def _sync_mention_popup_host(self) -> None:
        host = self.window()
        if host is None or self._mention_popup.parentWidget() is host:
            return
        self._mention_popup.setParent(host)
        self._mention_popup.hide()

    def reset_draft(self) -> None:
        self.clear()
        self._playhead_pill_locked = False
        self._apply_default_cursor_format()
        self._entry_id = uuid.uuid4().hex[:16]
        self._media_dir = note_media_entry_dir(self._item_root, self._entry_id)

    def set_playhead_pill_locked(self, locked: bool) -> None:
        self._playhead_pill_locked = bool(locked)

    def playhead_pill_locked(self) -> bool:
        return self._playhead_pill_locked

    def load_entry_for_edit(self, entry_id: str, *, body_html: str, plain_fallback: str = "") -> None:
        """Reuse an existing note id (and media folder) when editing."""
        eid = (entry_id or "").strip()
        if not eid:
            return
        self._entry_id = eid
        self._media_dir = note_media_entry_dir(self._item_root, self._entry_id)
        self.clear()
        self._apply_default_cursor_format()
        html = (body_html or "").strip()
        if not html and plain_fallback:
            html = f"<p>{plain_fallback.replace(chr(10), '<br>')}</p>"
        if html:
            self.setHtml(note_html_for_display(html))
            self._apply_default_cursor_format()

    @property
    def draft_entry_id(self) -> str:
        return self._entry_id

    def has_content(self) -> bool:
        plain = self.toPlainText().strip()
        if not plain:
            return False
        span = self._playhead_span()
        if span is None:
            return True
        cursor = QTextCursor(self.document())
        cursor.setPosition(span[0])
        cursor.setPosition(span[1], QTextCursor.MoveMode.KeepAnchor)
        playhead_plain = cursor.selectedText().replace("\u2029", "\n").strip()
        rest = plain
        if playhead_plain and rest.startswith(playhead_plain):
            rest = rest[len(playhead_plain) :].strip()
        elif playhead_plain:
            rest = rest.replace(playhead_plain, "", 1).strip()
        return bool(rest)

    def sync_playhead_pill(self, frame: int, fps: float) -> None:
        if self._playhead_pill_locked:
            return
        label = format_frame_pill_label(frame, fps)
        href = time_href_for_playhead(frame)
        snippet = f" {label} "
        fmt = self._time_pill_char_format(href, kind="playhead")
        spans = self._iter_playhead_spans()
        cursor = QTextCursor(self.document())
        if spans:
            start, end = spans[0]
            cursor.setPosition(start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(snippet, fmt)
            for extra_start, extra_end in reversed(self._iter_playhead_spans()[1:]):
                extra = QTextCursor(self.document())
                extra.setPosition(extra_start)
                extra.setPosition(extra_end, QTextCursor.MoveMode.KeepAnchor)
                extra.removeSelectedText()
        else:
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.insertText(snippet, fmt)
            cursor.insertText(" ", self._body_char_format())
        self._apply_default_cursor_format()
        self._clamp_cursor_outside_playhead()

    def _iter_playhead_spans(self) -> list[tuple[int, int]]:
        out: list[tuple[int, int]] = []
        block = self.document().firstBlock()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                if frag.isValid():
                    href = frag.charFormat().anchorHref()
                    if is_playhead_time_href(href):
                        out.append((frag.position(), frag.position() + frag.length()))
                it += 1
            block = block.next()
        return out

    def _playhead_span(self) -> tuple[int, int] | None:
        spans = self._iter_playhead_spans()
        return spans[0] if spans else None

    def _clamp_cursor_outside_playhead(self) -> None:
        span = self._playhead_span()
        if span is None:
            return
        pstart, pend = span
        cursor = self.textCursor()
        if cursor.hasSelection():
            return
        pos = cursor.position()
        if pstart < pos < pend:
            cursor.setPosition(pend)
            self.setTextCursor(cursor)

    def _selection_overlaps_playhead(self) -> bool:
        span = self._playhead_span()
        if span is None:
            return False
        cursor = self.textCursor()
        if not cursor.hasSelection():
            return False
        s, e = sorted((cursor.selectionStart(), cursor.selectionEnd()))
        pstart, pend = span
        return s < pend and e > pstart

    def _key_would_damage_playhead(self, event: QKeyEvent) -> bool:
        span = self._playhead_span()
        if span is None:
            return False
        pstart, pend = span
        key = event.key()
        mods = event.modifiers()
        if key in (Qt.Key.Key_Backspace, Qt.Key.Key_Delete):
            if self._selection_overlaps_playhead():
                return True
            pos = self.textCursor().position()
            if key == Qt.Key.Key_Backspace and pstart < pos <= pend:
                return True
            if key == Qt.Key.Key_Delete and pstart <= pos < pend:
                return True
        if key == Qt.Key.Key_X and mods & Qt.KeyboardModifier.ControlModifier:
            if self._selection_overlaps_playhead():
                return True
        return False

    def body_html(self) -> str:
        return self.document().toHtml().strip()

    def plain_text(self) -> str:
        return strip_html_preview(self.body_html())

    def mention_ids(self) -> tuple[str, ...]:
        return parse_mentions_from_html(self.body_html())

    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        if source.hasImage():
            img = source.imageData()
            if isinstance(img, QImage) and not img.isNull():
                self._insert_image(img)
                return
        if source.hasText():
            cursor = self.textCursor()
            cursor.setCharFormat(self._body_char_format())
            cursor.insertText(source.text())
            self.setTextCursor(cursor)
            return

    def _insert_image(self, img: QImage) -> None:
        full = _scale_image(img.convertToFormat(QImage.Format.Format_RGBA8888), _FULL_IMG_MAX_PX)
        self._media_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex[:12]}.png"
        abs_path = self._monostudio_dir / NOTE_MEDIA_DIR / self._entry_id / fname
        abs_path.parent.mkdir(parents=True, exist_ok=True)
        if not full.save(str(abs_path), "PNG"):
            return
        display = square_framed_note_image(
            full, hovered=False, device_pixel_ratio=self._thumb_dpr()
        )
        url = QUrl.fromLocalFile(str(abs_path))
        self.document().addResource(QTextDocument.ResourceType.ImageResource, url, display)
        fmt = QTextImageFormat()
        fmt.setName(url.toString())
        fmt.setWidth(NOTE_IMG_BOX_SIZE)
        fmt.setHeight(NOTE_IMG_BOX_SIZE)
        fmt.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignTop)
        cursor = self.textCursor()
        cursor.insertImage(fmt)
        cursor.setCharFormat(self._body_char_format())
        cursor.insertText(" ")
        self.setTextCursor(cursor)
        normalize_note_document_spacing(self.document())

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if (
            event.type() == QEvent.Type.KeyPress
            and isinstance(event, QKeyEvent)
            and self._mention_popup.isVisible()
        ):
            fw = QApplication.focusWidget()
            if fw is not None and (fw is self or self.isAncestorOf(fw)):
                if self._try_handle_mention_key(event):
                    return True
        if (
            watched is self
            and event.type() == QEvent.Type.ShortcutOverride
            and isinstance(event, QKeyEvent)
            and self._mention_popup.isVisible()
            and event.key()
            in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
                Qt.Key.Key_Tab,
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
                Qt.Key.Key_Escape,
            )
        ):
            event.accept()
        return super().eventFilter(watched, event)

    def event(self, event: QEvent) -> bool:  # type: ignore[override]
        if (
            event.type() == QEvent.Type.ShortcutOverride
            and self._mention_popup.isVisible()
            and isinstance(event, QKeyEvent)
            and event.key()
            in (
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
                Qt.Key.Key_Tab,
                Qt.Key.Key_Up,
                Qt.Key.Key_Down,
                Qt.Key.Key_Escape,
            )
        ):
            event.accept()
        return super().event(event)

    def _try_handle_mention_key(self, event: QKeyEvent) -> bool:
        if not self._mention_popup.isVisible():
            return False
        key = event.key()
        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Tab):
            user = self._mention_popup.selected_user()
            if user is not None:
                self._insert_mention(user)
                event.accept()
                return True
        if key == Qt.Key.Key_Up:
            self._mention_popup.move_selection(-1)
            event.accept()
            return True
        if key == Qt.Key.Key_Down:
            self._mention_popup.move_selection(1)
            event.accept()
            return True
        if key == Qt.Key.Key_Escape:
            self._hide_mention_popup()
            event.accept()
            return True
        return False

    def _ensure_mention_key_capture(self, active: bool) -> None:
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return
        if active:
            if self._mention_key_capture_host is not app:
                if self._mention_key_capture_host is not None:
                    self._mention_key_capture_host.removeEventFilter(self)
                app.installEventFilter(self)
                self._mention_key_capture_host = app
        elif self._mention_key_capture_host is not None:
            self._mention_key_capture_host.removeEventFilter(self)
            self._mention_key_capture_host = None

    def _hide_mention_popup(self) -> None:
        self._mention_popup.hide()
        self._ensure_mention_key_capture(False)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if self._try_handle_mention_key(event):
            return
        if self._key_would_damage_playhead(event):
            event.accept()
            return
        if event.text() and event.text().isprintable():
            cursor = self.textCursor()
            if not cursor.charFormat().isAnchor():
                cursor.mergeCharFormat(self._body_char_format())
                self.setTextCursor(cursor)
        super().keyPressEvent(event)
        self._maybe_show_mention_popup()
        self._clamp_cursor_outside_playhead()

    def _body_char_format(self, *, weight: QFont.Weight = QFont.Weight.Normal) -> QTextCharFormat:
        fmt = QTextCharFormat()
        font = QFont(NOTE_BODY_FONT)
        font.setWeight(weight)
        fmt.setFont(font)
        fmt.setForeground(QColor(NOTE_BODY_COLOR))
        fmt.setAnchor(False)
        return fmt

    def _apply_default_cursor_format(self) -> None:
        cursor = self.textCursor()
        cursor.setCharFormat(self._body_char_format())
        self.setTextCursor(cursor)

    def _maybe_show_mention_popup(self) -> None:
        cursor = self.textCursor()
        block = cursor.block()
        text_before = block.text()[: cursor.positionInBlock()]
        at_idx = text_before.rfind("@")
        if at_idx < 0:
            self._hide_mention_popup()
            return
        query = text_before[at_idx + 1 :]
        if " " in query or "\n" in query:
            self._hide_mention_popup()
            return
        self._mention_start = block.position() + at_idx
        cr = self.cursorRect()
        global_anchor = self.viewport().mapToGlobal(cr.bottomLeft())
        if self._mention_popup.show_filtered(query=query):
            self._place_mention_popup(global_anchor)
            self._ensure_mention_key_capture(True)
        else:
            self._ensure_mention_key_capture(False)

    def _place_mention_popup(self, global_anchor: QPoint) -> None:
        popup = self._mention_popup
        parent = popup.parentWidget()
        if parent is None:
            return
        editor_rect = QRect(self.mapToGlobal(QPoint(0, 0)), self.size())
        cr = self.cursorRect()
        cursor_global = self.viewport().mapToGlobal(cr.topLeft())
        anchor_rect = QRect(cursor_global, cr.size())
        position_child_popup_near_global_point(
            popup,
            parent,
            QPoint(global_anchor.x(), global_anchor.y() + 2),
            anchor_rect_global=anchor_rect,
            bounds=editor_rect,
            gap=2,
        )
        popup.raise_()

    def insert_time_pill(self, label: str, href: str, *, kind: NoteTimeKind = "frame") -> None:
        snippet = (label or "").strip()
        href = (href or "").strip()
        if not snippet or not href:
            return
        cursor = self.textCursor()
        fmt = self._time_pill_char_format(href, kind=kind)
        cursor.insertText(f" {snippet} ", fmt)
        cursor.setCharFormat(self._body_char_format())
        cursor.insertText(" ")
        self.setTextCursor(cursor)

    def insert_inline_reference(self, text: str) -> None:
        """Legacy plain reference — prefer insert_time_pill with structured href."""
        snippet = (text or "").strip()
        if not snippet:
            return
        cursor = self.textCursor()
        fmt = self._body_char_format()
        fmt.setFontFamilies(["JetBrains Mono"])
        fmt.setForeground(QColor("#a1a1aa"))
        cursor.insertText(snippet, fmt)
        cursor.setCharFormat(self._body_char_format())
        cursor.insertText(" ")
        self.setTextCursor(cursor)

    def _time_pill_char_format(self, href: str, *, kind: NoteTimeKind) -> QTextCharFormat:
        fmt = self._body_char_format(weight=QFont.Weight.DemiBold)
        fmt.setFontFamilies(["JetBrains Mono"])
        fmt.setAnchor(True)
        fmt.setAnchorHref(href)
        if kind == "range":
            fmt.setForeground(QColor("#93c5fd"))
            fmt.setBackground(QColor(37, 99, 235, 72))
        elif kind == "marker":
            fmt.setForeground(QColor("#f9a8d4"))
            fmt.setBackground(QColor(244, 114, 182, 56))
        elif kind == "playhead":
            fmt.setForeground(QColor("#fde68a"))
            fmt.setBackground(QColor(245, 158, 11, 62))
        else:
            fmt.setForeground(QColor("#e4e4e7"))
            fmt.setBackground(QColor(63, 63, 70, 96))
        return fmt

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            href = self.anchorAt(event.position().toPoint() if hasattr(event, "position") else event.pos())
            if href and is_time_note_href(href):
                self.time_anchor_clicked.emit(href)
                event.accept()
                return
        super().mouseReleaseEvent(event)

    def _insert_mention(self, user: StudioUser) -> None:
        self._hide_mention_popup()
        cursor = self.textCursor()
        end = cursor.position()
        if self._mention_start >= 0:
            cursor.setPosition(self._mention_start)
            cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            cursor.removeSelectedText()
        name = user.name or user.id
        fmt = self._body_char_format(weight=QFont.Weight.DemiBold)
        fmt.setAnchor(True)
        fmt.setAnchorHref(mention_href_for_user(user.id))
        fmt.setForeground(QColor(user.color_hex or "#60a5fa"))
        cursor.insertText(f"@{name}", fmt)
        cursor.setCharFormat(self._body_char_format())
        cursor.insertText(" ")
        self.setTextCursor(cursor)
        self._mention_start = -1

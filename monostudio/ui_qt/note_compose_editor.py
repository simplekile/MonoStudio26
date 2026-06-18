"""Rich-text compose editor for notes: paste images + @mentions."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, QUrl
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
from monostudio.core.user_identity import StudioUser, read_roster
from monostudio.ui_qt.note_mention_popup import NoteMentionPopup
from monostudio.ui_qt.note_image_hit_test import image_href_at_widget_pos
from monostudio.ui_qt.note_image_viewer_dialog import NoteImageViewerDialog
from monostudio.ui_qt.popup_position import position_child_popup_near_global_point
from monostudio.ui_qt.style import monos_font

_FULL_IMG_MAX_PX = 1920
NOTE_LINE_MIN_H = NOTE_IMG_BOX_SIZE + 6
NOTE_BLOCK_BOTTOM_MARGIN = 6
NOTE_BODY_FONT_SIZE = 11
NOTE_BODY_FONT = monos_font("Inter", NOTE_BODY_FONT_SIZE, QFont.Weight.Normal)
NOTE_BODY_COLOR = "#e4e4e7"
_LH_MINIMUM = QTextBlockFormat.LineHeightTypes.MinimumHeight.value
NOTE_RICH_TEXT_STYLESHEET = (
    f"body, p, li, a, span {{ font-family: Inter; font-size: {NOTE_BODY_FONT_SIZE}pt; }}"
    f"body, p, li {{ margin-top: 0px; margin-bottom: 6px; line-height: 1.5; color: {NOTE_BODY_COLOR}; }}"
    "a { text-decoration: none; }"
    "img { vertical-align: top; }"
)


def normalize_note_document_spacing(doc: QTextDocument) -> None:
    """Ensure blocks with inline images have enough line height and paragraph gap."""
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
        bf = block.blockFormat()
        changed = False
        if has_image:
            min_h = float(NOTE_LINE_MIN_H)
            if bf.lineHeightType() != _LH_MINIMUM or bf.lineHeight() < min_h:
                bf.setLineHeight(min_h, _LH_MINIMUM)
                changed = True
        if bf.bottomMargin() < NOTE_BLOCK_BOTTOM_MARGIN:
            bf.setBottomMargin(NOTE_BLOCK_BOTTOM_MARGIN)
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
        self._apply_default_cursor_format()
        self._entry_id = uuid.uuid4().hex[:16]
        self._media_dir = note_media_entry_dir(self._item_root, self._entry_id)

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
        return bool(self.toPlainText().strip())

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
        if event.text() and event.text().isprintable():
            cursor = self.textCursor()
            if not cursor.charFormat().isAnchor():
                cursor.mergeCharFormat(self._body_char_format())
                self.setTextCursor(cursor)
        super().keyPressEvent(event)
        self._maybe_show_mention_popup()

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

"""libmpv OpenGL render widget — no wid / native HWND embed."""

from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QMetaObject, QTimer, Signal, Slot
from PySide6.QtGui import QOpenGLContext
from PySide6.QtOpenGLWidgets import QOpenGLWidget

logger = logging.getLogger(__name__)


class MpvRenderWidget(QOpenGLWidget):
    """Render mpv video frames into a Qt OpenGL surface (vo=libmpv)."""

    render_ready = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        self._player = None
        self._render_ctx = None
        self._render_ready = False
        self._render_ready_emitted = False
        self._update_fn_wrapper = None
        self._proc_fn_wrapper = None
        self._painting = False
        self._frame_rendered = False

    def is_render_ready(self) -> bool:
        return bool(self._render_ready and self._render_ctx is not None)

    def bind_player(self, player) -> None:
        """Store mpv core; render context is created in paintGL / initializeGL."""
        self._player = player
        if self.isValid():
            self.update()

    def _init_render_if_needed(self) -> bool:
        if self._render_ctx is not None or self._player is None:
            return self._render_ctx is not None
        try:
            self._ensure_render_context()
        except Exception as e:
            logger.warning("mpv render context init failed: %s", e)
            self._render_ready = False
            return False
        self._schedule_render_ready()
        return True

    def request_render(self) -> None:
        if self.isVisible() and not self._painting:
            self.update()

    def release_gl(self) -> None:
        self._render_ready = False
        self._render_ready_emitted = False
        self._frame_rendered = False
        if self._render_ctx is not None:
            try:
                ctx = self.context()
                if ctx is not None and ctx.isValid():
                    self.makeCurrent()
                self._render_ctx.update_cb = None
                self._render_ctx.free()
            except Exception as e:
                logger.debug("mpv render ctx free: %s", e)
            finally:
                self._render_ctx = None
                self._update_fn_wrapper = None
                self._proc_fn_wrapper = None
                try:
                    self.doneCurrent()
                except Exception:
                    pass
        self._player = None

    def has_rendered_frame(self) -> bool:
        return self._frame_rendered

    def _schedule_render_ready(self) -> None:
        if self._render_ready_emitted or not self.is_render_ready():
            return
        self._render_ready_emitted = True
        QTimer.singleShot(0, self.render_ready.emit)

    def _ensure_render_context(self) -> None:
        if self._render_ctx is not None or self._player is None:
            return
        from mpv import MpvGlGetProcAddressFn, MpvRenderContext

        self._proc_fn_wrapper = MpvGlGetProcAddressFn(self._resolve_gl_proc)
        self._render_ctx = MpvRenderContext(
            self._player,
            "opengl",
            opengl_init_params={"get_proc_address": self._proc_fn_wrapper},
            advanced_control=False,
        )
        self._render_ctx.update_cb = self._on_mpv_update
        self._render_ready = True
        logger.debug("mpv render context ready (%dx%d)", self.width(), self.height())

    def _resolve_gl_proc(self, _ctx, name):
        ctx = QOpenGLContext.currentContext()
        if ctx is None:
            return None
        raw = name.decode("utf-8", errors="replace") if isinstance(name, bytes) else str(name)
        addr = ctx.getProcAddress(raw)
        if addr is None:
            return None
        try:
            return int(addr)
        except (TypeError, ValueError):
            return None

    def _on_mpv_update(self) -> None:
        if self._render_ctx is None or self._painting:
            return
        try:
            QMetaObject.invokeMethod(
                self,
                "_maybe_update",
                Qt.ConnectionType.QueuedConnection,
            )
        except RuntimeError:
            pass

    @Slot()
    def _maybe_update(self) -> None:
        if not self.isVisible() or self._painting:
            return
        self.update()

    def initializeGL(self) -> None:
        self._init_render_if_needed()

    def paintGL(self) -> None:
        if self._painting:
            return
        if not self._init_render_if_needed() or self._render_ctx is None:
            return
        self._painting = True
        try:
            fbo = int(self.defaultFramebufferObject())
            w = max(1, self.width())
            h = max(1, self.height())
            self._render_ctx.render(
                flip_y=True,
                opengl_fbo={"w": w, "h": h, "fbo": fbo},
                block_for_target_time=False,
            )
            try:
                self._render_ctx.report_swap()
            except Exception:
                pass
            self._frame_rendered = True
        except Exception as e:
            logger.debug("mpv paintGL: %s", e)
        finally:
            self._painting = False

    def resizeGL(self, w: int, h: int) -> None:
        _ = w, h
        if self._render_ctx is not None and not self._painting:
            self.update()

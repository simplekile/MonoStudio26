"""
Windows Explorer shell thumbnail extraction for Inspector preview (Inbox/Project Guide).
Uses IShellItemImageFactory when available (comtypes + pywin32 on Windows); otherwise no-op.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QPixmap

logger = logging.getLogger(__name__)

_SHELL_THUMB_AVAILABLE = False
_shell_thumb_fn = None

# COM init once per thread (COINIT_APARTMENTTHREADED = 0x2)
_COINIT_APARTMENTTHREADED = 0x2


def _init_shell_thumb() -> bool:
    global _SHELL_THUMB_AVAILABLE, _shell_thumb_fn
    if _shell_thumb_fn is not None:
        return _SHELL_THUMB_AVAILABLE
    if sys.platform != "win32":
        _shell_thumb_fn = lambda p, s: None
        return False
    try:
        import ctypes
        from ctypes import POINTER, byref, cast, windll, c_void_p
        from ctypes.wintypes import SIZE, UINT, HANDLE, HBITMAP
        from comtypes import GUID, COMMETHOD, HRESULT
        from comtypes.hresult import S_OK
        import win32ui
    except ImportError as e:
        logger.warning(
            "Shell thumbnail (Windows Explorer) unavailable: missing dependency. "
            "Install with: pip install comtypes  (pywin32 already required on Windows)"
        )
        _shell_thumb_fn = lambda p, s: None
        return False

    try:
        from ctypes import c_wchar_p

        from comtypes import IUnknown

        ole32 = windll.ole32
        shell32 = windll.shell32
        gdi32 = windll.gdi32

        # COM: ensure thread has COM initialized (needed when called from Qt UI thread)
        def _ensure_com() -> None:
            try:
                ole32.CoInitializeEx(c_void_p(0), _COINIT_APARTMENTTHREADED)
            except Exception:
                pass

        shell32.SHCreateItemFromParsingName.argtypes = [
            c_wchar_p,
            c_void_p,
            POINTER(GUID),
            POINTER(c_void_p),
        ]
        shell32.SHCreateItemFromParsingName.restype = ctypes.c_long

        class IShellItemImageFactory(IUnknown):
            _iid_ = GUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")
            _methods_ = [
                COMMETHOD(
                    [],
                    HRESULT,
                    "GetImage",
                    (["in"], SIZE, "size"),
                    (["in"], UINT, "flags"),
                    (["out"], POINTER(HBITMAP), "phbm"),
                ),
            ]

        SIIGBF_RESIZETOFIT = 0
        IID_IShellItemImageFactory = GUID("{bcc18b79-ba16-442f-80c4-8a59c30c463b}")

        def get_hbitmap(file_path: str, size_px: int) -> int | None:
            _ensure_com()
            path_win = str(Path(file_path).resolve()).replace("/", "\\")
            out_ptr = c_void_p()
            hr = shell32.SHCreateItemFromParsingName(
                path_win,
                None,
                byref(IID_IShellItemImageFactory),
                byref(out_ptr),
            )
            if hr != 0 or not out_ptr.value:
                logger.debug("SHCreateItemFromParsingName hr=0x%x path=%s", hr & 0xFFFFFFFF, path_win[:80])
                return None
            siif = cast(out_ptr, POINTER(IShellItemImageFactory))
            try:
                hbm = HBITMAP()
                hr = siif.GetImage(SIZE(size_px, size_px), SIIGBF_RESIZETOFIT, byref(hbm))
                if hr != S_OK or not hbm or not hbm.value:
                    logger.debug("GetImage hr=0x%x", hr & 0xFFFFFFFF if hr is not None else None)
                    return None
                return int(hbm.value)
            finally:
                try:
                    siif.Release()
                except Exception:
                    pass

        def get_windows_shell_thumbnail_impl(path: Path, size_px: int) -> "QPixmap | None":
            from PySide6.QtGui import QImage, QPixmap

            try:
                hbm = get_hbitmap(str(path), size_px)
                if not hbm:
                    return None
                try:
                    pyCBitmap = win32ui.CreateBitmapFromHandle(hbm)
                    info = pyCBitmap.GetInfo()
                    w, h = info["bmWidth"], info["bmHeight"]
                    if w <= 0 or h <= 0:
                        return None
                    data = pyCBitmap.GetBitmapBits(True)
                    if not data:
                        return None
                    img = QImage(
                        data,
                        w,
                        h,
                        w * 4,
                        QImage.Format.Format_ARGB32,
                    ).copy()
                    if img.isNull():
                        return None
                    return QPixmap.fromImage(img)
                finally:
                    gdi32.DeleteObject(c_void_p(hbm))
            except Exception as e:
                logger.debug("Shell thumbnail failed for %s: %s", path, e)
                return None

        _shell_thumb_fn = get_windows_shell_thumbnail_impl
        _SHELL_THUMB_AVAILABLE = True
        return True
    except Exception as e:
        logger.warning("Shell thumbnail init failed: %s", e)
        _shell_thumb_fn = lambda p, s: None
        return False


def get_windows_shell_thumbnail(path: Path, size_px: int = 512) -> "QPixmap | None":
    """Return QPixmap from Windows shell thumbnail for the given file, or None if unavailable."""
    if not path or not path.is_file():
        return None
    if not _init_shell_thumb():
        return None
    return _shell_thumb_fn(path, size_px)

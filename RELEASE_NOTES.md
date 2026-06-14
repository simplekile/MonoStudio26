# MonoStudio 26 — Release v26.15.10

## Highlights

- **Settings → Updates**: Nút Download/Latest tự đo width theo label — không bị cắt chữ; loading bar + cancel đồng bộ; FFmpeg row cùng pattern.
- **Settings → General**: Sửa tab index sau khi thêm Hotkeys (Updates tab mở đúng).
- **Startup splash**: Thu nhỏ 70% (672×378), typography/margin scale theo tỷ lệ.

## Changes in this release

- fix: `settings_dialog.py` — `_apply_update_action_width`, cancel btn QSS, tier-2 Updates index.
- fix: `splash.py` — compact splash dimensions.
- fix: `style.py` — update action/cancel button padding.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

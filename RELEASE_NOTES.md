# MonoStudio 26 — Release v26.14.3

## Highlights

- **System tray**: Thu nhỏ xuống tray, menu tray (mở/ẩn/thoát), tùy chọn hành vi khi đóng cửa sổ (thoát / minimize to tray / hỏi mỗi lần).
- **Single instance**: Mở app lần hai sẽ đưa cửa sổ đang chạy lên trước (không nhân đôi process).
- **Windows autostart**: Bật/tắt chạy cùng Windows từ Settings (HKCU Run).
- **Toast**: Tinh chỉnh focus bridge khi bấm notification.

## Changes in this release

- feat: `tray_manager.py`, `close_behavior_dialog.py`, `tray_preferences.py`.
- feat: `single_instance.py`, `app_launch.py`, `windows_autostart.py`.
- feat: `main_window.py`, `settings_dialog.py`, `app.py` — tray, close, autostart wiring.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

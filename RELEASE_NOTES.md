# MonoStudio 26 — Release v26.14.6

## Highlights

- **Cấu hình local đúng chỗ**: `app_settings.json` (session, geometry, user pins) lưu `%LOCALAPPDATA%\MonoStudio\config` — cài lại app không “dính” user cũ từ thư mục cài; tự migrate file legacy một lần.
- **Sign-in**: Pre-select theo lần đăng nhập gần nhất, rồi device binding; luôn cập nhật device khi Sign in (kể cả không tick Stay signed in).

## Changes in this release

- feat: `app_paths.py` — `get_app_user_config_dir`, `migrate_app_settings_if_needed`.
- feat: `user_identity.py` — `last_signed_in`, đường dẫn config mới.
- fix: `main_window.py`, `user_identity_dialog.py`, `app.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

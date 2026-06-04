# MonoStudio 26 — Release v26.14.4

## Highlights

- **Tray mini popup**: Click tray icon — popup Recent tasks, entities, notifications (thumbnail + DCC badges); mở file / nhảy vào app từ tray.
- **Tray icon badge**: Số unread mention/notification trên icon tray.
- **Notifications**: Store/unread đồng bộ với tray và dropdown; tinh chỉnh list UI.

## Changes in this release

- feat: `tray_mini_popup.py`, `tray_icon_badges.py`; `tray_manager.py` mở rộng.
- feat: `main_window.py` — wiring task/entity/notification từ tray.
- fix/style: `notification/store.py`, dropdown/list, `sidebar.py`, `top_bar.py`, `style.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

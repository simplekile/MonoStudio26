# MonoStudio 26 — Release v26.14.5

## Highlights

- **Single instance fix**: Kiểm tra instance trùng ngay khi khởi động (trước splash/tray); gửi “raise” tới cửa sổ đang chạy ổn định hơn; không bỏ lỡ focus khi callback chưa gắn.

## Changes in this release

- fix: `single_instance.py` — signal trước khi listen, pending raise, timeout connect/write.
- fix: `app.py` — `acquire_single_instance()` gọi sớm hơn trong `main()`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

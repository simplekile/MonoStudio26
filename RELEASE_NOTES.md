# MonoStudio 26 — Release v26.17.5

## Highlights

- **Paste work file**: Confirm dialog trước khi paste work file thành version mới.
- **Deep links**: Resolve entity trên Shots (flat folders) ổn định hơn; tests cập nhật.
- **Page loading**: Deferred tree refresh cho Inbox / Project Guide — ít jank khi đổi trang.
- **Thumbnails / Inspector / Main View**: Preview & list polish; trash page; worker loop đơn giản hóa.
- **Pipeline presets**: `types_and_presets.json` / registry tweaks.

## Changes in this release

- feat: `paste_work_file_confirm_dialog.py`.
- fix: `deep_link.py`, `main_window.py`, `thumbnails.py`, `inspector.py`, `inbox_split_view.py`, `ui_worker_loop.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg** / **DJV View**: Settings → General / Updates.

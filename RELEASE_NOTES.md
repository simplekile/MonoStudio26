# MonoStudio 26 — Release v26.17.3

## Highlights

- **Deep links**: Short entity id (`e=`) thay path dài; page alias ngắn; resolve + tests; foreground focus khi mở từ instance thứ hai (`window_focus`).
- **Review / preview**: Sequence & review media ổn định hơn; draw overlay; proxy worker; video preview dialog / switch popup / player backend.
- **Tray & drop**: Tray mini popup; external drop host; link reveal flash.
- **Pipeline / UI**: Structure presets; hotkeys; inspector / sidebar / inbox-outbox-guide; style tokens.

## Changes in this release

- feat: `window_focus.py`, short deep-link entity ids, `test_deep_link.py`, `test_external_drop_host.py`.
- fix: `video_preview_dialog.py`, `review_draw_overlay.py`, `main_window.py`, `sequence_preview.py`, `review_media.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg** / **DJV View**: Settings → General / Updates.

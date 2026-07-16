# MonoStudio 26 — Release v26.17.4

## Highlights

- **Focus timer / Pomodoro**: Plugin mới — engine, utility window, Settings page, TopBar/tray entry, tray + in-app notifications.
- **Project health**: Scan & cleanup autosaves, Blender backups, wrong-extension work files, Houdini backup dirs; dialog UI.
- **Review sidecar**: Unified `.monos/*.review.json` cho ranges, markers, draw; migrate legacy review files.
- **Deep links / reveal**: Link reveal flash, deep-link parsing/tests, foreground/window focus polish.
- **Review / preview**: Review draw/media tests, video media robustness, preview/proxy/switch popup refinements.

## Changes in this release

- feat: `monostudio/plugins/pomodoro/*`, `test_pomodoro_engine.py`, `plan_pomodoro_plugin_v1.mdc`.
- feat: `project_health.py`, `item_health_scan.py`, `project_health_dialog.py`, `test_project_health.py`.
- fix: `review_sidecar.py`, `review_draw.py`, `review_media.py`, `video_media.py`, `main_window.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg** / **DJV View**: Settings → General / Updates.

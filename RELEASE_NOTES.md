# MonoStudio 26 — Release v26.17.0

## Highlights

- **DJV View sidecar**: Thay OpenRV — launch DJV View từ review; Settings → path + detect; registry fallback Windows.
- **OCIO sequence review**: Display transform ACEScg → view (PyOpenColorIO); bundled ACES 1.3 config; EXR/DPX/HDR plate decode.
- **Deep links**: `monostudio://` mở rộng — navigate + flash highlight (`link_reveal`); URL protocol & deep_link core.
- **Async UI workers**: `ui_worker_loop` + `schedule_reload_worker` — background tasks không block loading animation.
- **Video player settings**: Dialog cấu hình preview/playback; OCIO preview settings.
- **Explorer**: File sort; inbox/outbox/internal-check toolbar & split view cập nhật.
- **Build**: Installer script + publish tweaks; `opencolorio` dependency.

## Changes in this release

- feat: `djv_launch.py`, `ocio_display.py`, `ocio_preview_settings.py`, `video_player_settings_dialog.py`, `link_reveal.py`, `explorer_file_sort.py`.
- feat: `ui_worker_loop.py`, `schedule_reload_worker.py`, `deep_link.py`, `main_window.py`, `main_view.py`.
- remove: `openrv_launch.py` (replaced by DJV).

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg** / **DJV View**: Settings → General / Updates.

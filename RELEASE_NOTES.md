# MonoStudio 26 — Release v26.16.4

## Highlights

- **Review player**: Frameless resize (WM_NCHITTEST + edge handles); chrome/wheel routing; export progress; review switch popup; footer hint & settings polish.
- **OpenRV sidecar**: Launch OpenRV (rv.exe) từ review; Settings → path + detect status.
- **Command palette**: Starred items (persist per project); palette UI/search cải thiện.
- **Settings / Updates**: Extra-repo checker mở rộng; mpv install tweak; Inspector preview options.
- **Explorer / Main View**: Thumbnail loader; project guide reader; pipeline view models refresh.

## Changes in this release

- feat: `openrv_launch.py`, `palette_stars_store.py`, `project_guide_reader.py`, `video_review_switch_popup.py`.
- fix: `video_preview_dialog.py`, `frameless_resize.py`, `command_palette_dialog.py`, `settings_dialog.py`, `main_window.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg** / **OpenRV**: Settings → General / Updates.

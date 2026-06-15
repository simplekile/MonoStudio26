# MonoStudio 26 — Release v26.16.0

## Highlights

- **Video preview & review**: Dialog phát video với mpv embed (fallback Qt / external); scrubber, transport, frame/timecode; multi-range review notes; export đoạn; sidecar ranges.
- **libmpv**: Settings → Inspector/Preview — cấu hình backend, đường dẫn mpv DLL; Get/Install portable libmpv (giống FFmpeg); optional bundle `tools/mpv/` lúc build.
- **Sequence preview**: Nâng cấp sequence preview dialog; tích hợp luồng review.
- **Entry points**: Mở preview từ Inbox, Project Guide, Inspector, Main View.
- **UI**: Review tools panel, range list, dialog geometry/size grip; QSS video preview; lucide icons (skip-back/forward, sync, …).

## Changes in this release

- feat: `video_preview_dialog.py`, `video_player_backend.py`, `video_media.py`, `mpv_install.py`, `mpv_resolve.py`.
- feat: `review_tools_panel.py`, `video_range_list_widget.py`, `video_export_dialog.py`, `media_preview_transport.py`.
- feat: `settings_dialog.py`, `main_window.py`, `sequence_preview_dialog.py`, `build_installer.ps1`, `monostudio26.spec`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** (cho preview embed): Settings → Updates → libmpv → Get/Install, hoặc đặt `mpv-2.dll` trong thư mục tùy chọn.

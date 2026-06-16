# MonoStudio 26 — Release v26.16.1

## Highlights

- **Video review markers**: Marker list widget; gắn/ghi chú theo frame; export và scrubber ổn định hơn.
- **Video proxy**: Tự build H.264 proxy cho codec nặng (ProRes, DNxHD, HEVC…); disk cache full/range; dialog khi source quá nặng; worker nền.
- **Scrub & playback**: Scrubber cải thiện; player backend ổn định; settings proxy/playback mở rộng.
- **UI**: Volume/gauge icons; QSS marker list & proxy UI.

## Changes in this release

- feat: `video_marker_list_widget.py`, `video_proxy.py`, `video_proxy_cache.py`, `video_proxy_build_worker.py`, `video_proxy_heavy_dialog.py`.
- fix: `video_preview_dialog.py`, `video_preview_scrubber.py`, `video_player_backend.py`, `video_export_dialog.py`, `settings_dialog.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg**: Settings → Updates (proxy build cần FFmpeg).

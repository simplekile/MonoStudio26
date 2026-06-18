# MonoStudio 26 — Release v26.16.2

## Highlights

- **Unified review player**: Một dialog review — draw layers (brush/eraser/onion), note rail, timeline pills, seek theo time anchor; export mở rộng.
- **Main View review mode**: Shot cards hiển thị render/review summary; pipeline review browsing.
- **Internal check**: Trang staging `outbox/internal_check` (date folders) trước khi gửi; migrate legacy `review` folder.
- **Delivery**: Core reader cho `outbox/delivery/<recipient>/<date>/`.
- **Inbox / Outbox / Guide**: Drop dialog, readers, sidebar nav cập nhật; command palette & hotkeys.

## Changes in this release

- feat: `review_draw.py`, `review_media.py`, `video_review_draw_*`, `review_playback_backend.py`, `note_time_anchors.py`.
- feat: `internal_check_reader.py`, `internal_check_page_widget.py`, `delivery_reader.py`, `shot_review_card.py`.
- fix: `video_preview_dialog.py`, `main_view.py`, `main_window.py`, `sidebar.py`, `inbox_page_widget.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg**: Settings → Updates.

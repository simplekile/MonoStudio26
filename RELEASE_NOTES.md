# MonoStudio 26 — Release v26.16.3

## Highlights

- **Main View list v2**: Pipeline List Row (`QListView`) — delegate, layout, rubber-band selection, row paint, sortable header, drag preview.
- **Review mode badges**: Grid thumb badges — render version state (current/outdated) + schedule deadline trên thumbnail.
- **Internal check / delivery**: Reader & inbox drop cải thiện; tests coverage.
- **mpv render widget**: Widget render mpv tách riêng cho preview embed.
- **Inspector / Main View**: Tích hợp list mới; shot review card mở rộng.

## Changes in this release

- feat: `pipeline_list_view.py`, `pipeline_list_delegate.py`, `pipeline_list_header.py`, `pipeline_list_hit.py`, `pipeline_drag_preview.py`.
- feat: `grid_review_thumb_badges.py`, `mpv_render_widget.py`.
- fix: `main_view.py`, `pipeline_view_models.py`, `shot_review_card.py`, `internal_check_reader.py`, `inspector.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg**: Settings → Updates.

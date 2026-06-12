# MonoStudio 26 — Release v26.15.6

## Highlights

- **Inbox / Outbox / Project Guide explorer**: Grid card paint, async thumbnail loader (không decode trên UI thread), tag badges trên card, breadcrumb/filter cải thiện.
- **Project Guide tags**: Lọc theo tag, department tag, PureRef brand icon.
- **Dashboard**: Widget palette cho customize mode; bento host cập nhật.
- **App hotkeys**: Registry phím tắt tập trung + UI chỉnh trong Settings.
- **Thumbnails**: Explorer preview disk cache, spinner loading, HiDPI paint helpers.
- **UI**: Sidebar/nav refresh; Inspector ref tab; notification toast/overlay; style tokens.

## Changes in this release

- feat: `app_hotkeys.py`, `dashboard_widget_palette.py`, `explorer_thumbnail_loader.py`.
- feat: `inbox_split_view.py`, `inbox_grid_card_paint.py`, `inbox_list_row_paint.py`, `thumbnails.py`, `project_guide_tags.py`.
- feat: `dashboard_bento_host.py`, `inspector_ref_tab.py`, `sidebar.py`, `main_window.py`, `style.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

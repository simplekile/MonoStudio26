# MonoStudio 26 — Release v26.15.7

## Highlights

- **Project Guide tags**: Context menu submenu **Tags** trên explorer — gán/bỏ tag theo selection (checkbox + màu tag).
- **Reference page**: Tag filter badges trên chrome; click badge để bỏ lọc; giữ tree state khi đổi department.
- **Schedule / Dashboard sync**: Sidebar `schedule_visible_department_ids` đồng bộ whitelist department với timeline và dashboard metrics.
- **UI**: QSS `GuideTagMenuRow`; dashboard browse khôi phục filter panel khi rời trang.

## Changes in this release

- fix: `inbox_split_view.py` — guide tag submenu, multi-select tag assign/remove.
- fix: `reference_page_widget.py`, `main_window.py` — tag badges, tree state cache.
- fix: `sidebar.py` — schedule department visibility export.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

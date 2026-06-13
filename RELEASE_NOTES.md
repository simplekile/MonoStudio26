# MonoStudio 26 — Release v26.15.8

## Highlights

- **Dashboard refresh**: Reload metrics riêng (`_refresh_dashboard_page`) — không làm churn main view / Inspector khi cập nhật dashboard.
- **Schedule department whitelist**: Sidebar giữ universe department ids (mọi type) để resolve whitelist Schedule/Dashboard đúng.
- **Nav rail**: Flyout không nuốt click khi đang mở trên item owner.

## Changes in this release

- fix: `main_window.py`, `dashboard_page_widget.py` — targeted dashboard refresh on context switch.
- fix: `sidebar.py` — `_schedule_universe_department_ids`, rebuild on meta/registry.
- fix: `nav_rail_expand_item.py`, `sidebar_nav_rail.py` — flyout click routing.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

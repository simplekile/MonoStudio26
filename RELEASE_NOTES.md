# MonoStudio 26 — Release v26.15.9

## Highlights

- **Startup splash**: Màn splash Resolve-style (hero image + gradient panel, progress bar, tail statuses) khi mở app.
- **Dashboard**: Week workload strip (Next 7 Days); dept workload popover drill-down; overdue/skipped dialogs; stats & layout responsive mở rộng.
- **Schedule skip**: `schedule_skip.py` đồng bộ omitted status; dialog liệt kê item/dept skipped; metric trên timeline.
- **Overdue**: Dialog overdue entities — jump sang Main View hoặc Schedule.
- **Sidebar / Schedule**: Filter department cải thiện; timeline + view options; preset pipeline (rig status).

## Changes in this release

- feat: `splash.py`, `app.py`, `monostudio_data/images/splash_hero.png`.
- feat: `dashboard_week_strip.py`, `dashboard_responsive_row.py`, `dept_workload_popover.py`, `overdue_entities_dialog.py`, `skipped_schedule_dialog.py`, `schedule_skip.py`.
- feat: `dashboard_page_widget.py`, `project_dashboard_stats.py`, `schedule_planner.py`, `sidebar.py`, `main_window.py`, `schedule_timeline_widget.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

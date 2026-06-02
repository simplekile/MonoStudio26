# MonoStudio 26 — Release v26.14.0

## Highlights

- **Schedule — timeline & planning**: Trang Schedule với timeline Gantt, milestone, allocate/autoplan, template, bulk allocate, filter department; lưu `schedule` trong project; nhảy từ Dashboard/Inspector sang đúng entity + due date.
- **Dashboard**: Trang tổng quan project (stats, unscheduled, liên kết Schedule); sidebar filter đồng bộ với Schedule.
- **User system (serverless)**: Danh bạ studio trong `workspace/.monostudio/users.json` (Dropbox); danh tính cục bộ + device fingerprint; profile/avatar, quản lý team; author/assignee cho note và mention.
- **Notes nâng cao**: Soạn/xem note có mention (@user), ảnh inline, viewer; mention inbox; tích hợp Inspector và activity.
- **Notifications & activity**: Dropdown/danh sách thông báo mở rộng; activity log; footer app.
- **Navigation**: Sidebar/top bar — Dashboard, Schedule; nav pills; Inspector block lịch trình.

## Changes in this release

- feat: Core schedule — `project_schedule`, `schedule_document`, `schedule_planner`, `schedule_dept_filter`, `schedule_date_display`, `project_dashboard_stats`, `mention_inbox`, `user_identity`.
- feat: UI Schedule — `schedule_page_widget`, `schedule_timeline_widget`, dialogs (plan, allocate, autoplan, milestone, template, view options, legend).
- feat: UI Dashboard — `dashboard_page_widget`; jump Schedule từ Inspector.
- feat: UI User/Notes — `team_management_dialog`, `user_*` dialogs, `note_*` editors/viewers, `activity_log`, `app_footer`, `inspector_schedule_block`.
- feat: `main_window`, `sidebar`, `top_bar`, `main_view`, `inspector` — wiring pages, filters, notifications.
- feat: `item_comments`, `access_control`, `atomic_write`, `fs_watcher` — notes/mentions và ghi file an toàn trên Dropbox.
- style: `style.py`, Lucide icons (filter, flag, save, timer, …).
- docs: `rule_build_v1` — `RELEASE_NOTES.md` chỉ giữ một bản mới nhất.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

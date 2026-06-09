# MonoStudio 26 — Release v26.15.0

## Highlights

- **Discord webhooks**: Thông báo Inbox (assign/distribute), Outbox received, Schedule due — cấu hình trong Settings → Integrations; debounce và copy chuẩn.
- **Deep link `monostudio://`**: URL protocol (installer đăng ký HKCU); mở assign/entity từ link; local deep-link server khi app chạy.
- **Inbox redesign**: Split view, browse bar, date folders, grid/list card paint, toolbar; assignee picker + assign confirm; drag-drop ngoài Explorer.
- **Sidebar nav rail**: Điều hướng dạng rail mở rộng; Dashboard bento layout; production UI refresh (style, pills, popover position).
- **Schedule & assign**: Phân quyền schedule, notify assign; inspector schedule block; allocate dialog assignee.
- **Affinity DCC**: Hỗ trợ Affinity trong pipeline (`dcc_affinity`, template `.af`).
- **Notes**: Seen-by label; mention/assign alert format; roster user combo.

## Changes in this release

- feat: `discord_*.py`, `integrations_config.py`, `notification_copy.py`, `assign_inbox.py`, `deep_link*.py`, `url_protocol.py`.
- feat: Inbox/Outbox UI — `inbox_split_view`, `inbox_browse_bar`, `inbox_page_toolbar`, `outbox_history_dialog`, `external_drop*`.
- feat: `sidebar_nav_rail`, `dashboard_bento_host`, `dashboard_layout.py`, `project_picker_dialog`, `project_lifecycle.py`.
- feat: `schedule_permissions.py`, `schedule_assign_notify.py`, `assignee_picker_widget`, `roster_user_combo.py`.
- feat: Settings Integrations; installer `monostudio://` protocol; `dccs.json` + Affinity icon/template.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

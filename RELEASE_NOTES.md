# MonoStudio 26 — Release v26.15.5

## Highlights

- **Schedule target / goal status**: Trạng thái mục tiêu theo department trên timeline; menu đổi status, goal dialog, bulk bar actions; quick milestone popup; planner tính goal-met.
- **Department status presets**: Registry theo department (`department_status_registry`, presets trong pipeline); Inspector/Main View dùng workflow đúng từng dept.
- **Schedule timeline**: Cải thiện lớn — bulk dialogs, target status memory, tương tác bar/row.
- **Discord**: Test webhook từ Settings; copy/assign notify mở rộng.
- **UI**: Page loading bar; project status menu; entity ref pins; project picker; Main View refresh.

## Changes in this release

- feat: `department_status_registry.py`, `schedule_target_status_*`, `schedule_goal_status_dialog.py`, `schedule_bar_bulk_dialogs.py`, `schedule_quick_milestone_popup.py`.
- feat: `entity_ref_pins.py`, `discord_webhook_test.py`, `discord_notification_test_dialog.py`, `page_loading_bar.py`, `project_status_menu.py`, `view_item_mtime.py`.
- feat: `project_schedule.py`, `schedule_planner.py`, `schedule_assign_notify.py`, `schedule_timeline_widget.py`, `schedule_page_widget.py`, `main_view.py`, `main_window.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

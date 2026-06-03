# MonoStudio 26 — Release v26.14.1

## Highlights

- **Schedule history**: Lịch sử chỉnh sửa schedule (ai, khi nào, tóm tắt thay đổi); dialog xem lịch sử, mở profile tác giả từ roster.
- **Milestone dialog**: Thiết kế lại — tab Production range + Milestones, thêm/sửa milestone trong form, đồng bộ timeline.
- **Calendar date picker**: UI/UX lịch chọn ngày thống nhất (Schedule, Inbox drop, dialogs).
- **Notes & profiles**: Hàng author (avatar + tên roster); link mở profile studio từ note; cải thiện image viewer và mention popup.
- **Dashboard & timeline**: Cập nhật stats/tiles; tinh chỉnh timeline và điều hướng Schedule.

## Changes in this release

- feat: `schedule_history.py`, `schedule_history_dialog.py` — ghi/đọc `schedule_history.json` khi lưu schedule.
- feat: `schedule_milestone_dialog.py` — UI mới range + danh sách milestone.
- feat: `note_author_row.py`, `user_profile_view_dialog.py` — author row và xem profile từ note/history.
- feat: `item_comments.py` — `NoteAuthorVisual` / avatar từ roster.
- fix/style: `calendar_date_picker.py`, `style.py`, schedule dialogs, `dashboard_page_widget`, `schedule_timeline_widget`, `main_window`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

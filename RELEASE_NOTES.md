# MonoStudio 26 — Release v26.14.2

## Highlights

- **Sign-in / identity fix**: Gắn device mỗi lần Sign in (không phụ thuộc “Stay signed in”); bỏ auto-chọn user đầu tiên trong roster khi chỉ có 1 account — tránh luôn vào tài khoản tạo đầu.
- **Mention notifications**: Windows toast (@mention), tùy chọn delivery trong Settings; dropdown/list notification UI mới.
- **Notes**: Đánh dấu done, lịch sử chỉnh sửa, context menu; author row/avatar cải thiện.
- **Houdini DCC**: Cải thiện subprocess env / launch trên Windows.

## Changes in this release

- fix: `user_identity.py`, `user_identity_dialog.py` — device binding + pre-select.
- feat: `windows_toast.py`, `windows_toast_bridge.py`, `notification_preferences.py` — toast mention + focus app.
- feat: `note_done_toggle.py`, `note_edit_history_dialog.py`, `note_context_menu.py`; `item_comments.py` done/edit_history.
- feat: notification row widgets, `mention_alert_format.py`; `settings_section_widgets.py`, Settings notifications section.
- fix: `dcc_houdini.py`, `dcc_subprocess_env.py`; `requirements.txt` — `windows-toasts`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

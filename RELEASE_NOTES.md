# MonoStudio 26 — Release v26.15.3

## Highlights

- **Create Asset / Shot**: Dialog thiết kế lại — preview tên theo type/prefix, chọn department, tùy chọn work/publish subfolders.
- **Batch create**: Tạo nhiều asset hoặc shot một lần (danh sách tên, comma-separated); menu/context từ Main View.
- **Schedule timeline**: Tinh chỉnh nhỏ label/resize; nav rail hover.

## Changes in this release

- feat: `create_entry_dialogs.py` — `BatchCreateAssetDialog`, `BatchCreateShotDialog`, name preview helpers.
- feat: `main_window.py` — `_batch_create_assets`, `_batch_create_shots`, shared post-create flow.
- fix/style: `schedule_timeline_widget.py`, `nav_rail_expand_item.py`, `style.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

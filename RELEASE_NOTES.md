# MonoStudio 26 — Release v26.3.0

## Highlights

- **Double-click / Smart Open**: Khi item đã có work file (vd. Rizom) nhưng user chưa click active badge, double-click thumb mở đúng DCC có file (Rizom), không còn tạo folder + mở DCC đầu tiên trong list (vd. Blender).
- **Active DCC một nguồn**: Đọc active DCC qua `_item_active_dcc()` (open.json); path open.json qua `_open_metadata_path()`. App controller + Inspector dùng chung; ghi chỉ qua `main_view.set_active_dcc()` (Inspector chỉ emit, không ghi trùng).
- **Context menu (card)**: Icon "Open" = brand icon DCC (fallback work_file_dcc khi chưa chọn badge). Khi không có work file trong department: disable Open, Open With…, Copy Work Path + tooltip "No work file in this department."

## Changes in this release

- fix: Smart Open — khi department đã có work file, ưu tiên mở DCC từ scan (work_file_dcc / work_file_dccs), không dùng registry "first in list"
- refactor: Active DCC — main_view: `_open_metadata_path()`, dùng trong _item_active_dcc / _write_active_dcc / _item_last_opened_dcc; app_controller dùng _item_active_dcc + _open_metadata_path, _resolve_dcc(item_path, …) đọc active từ _item_active_dcc
- refactor: Inspector đổi active DCC chỉ emit; ghi file qua main_view.set_active_dcc (single write path)
- feat: Context menu — Open icon fallback work_file_dcc khi chưa có active_dcc; disable Open, Open With, Copy Work Path khi không có work file trong department

## Install

Download **MonoStudio26_Setup.exe** from the Assets below and run. The installer will close the app if it is running so the update can be applied.

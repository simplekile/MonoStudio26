# MonoStudio 26 — Release notes

## Build / release note (khi build & publish)

- **Commit trước khi build**: `build_installer.ps1` mặc định tự commit; dùng `-NoCommit` nếu đã commit tay. Version lấy từ git count + VERSION.
- **Changelog**: Cập nhật `RELEASE_NOTES.md` (version, Highlights, Changes) trước hoặc sau build; `publish_release.ps1` ưu tiên đọc file này.
- **Install path cho extra tools**: Bản cài MonoStudio mỗi lần chạy ghi đường dẫn cài vào `%LOCALAPPDATA%\MonoStudio\install_path.txt` — installer MonoFXSuite (và tool khác) đọc file này để điền đúng "Under MonoStudio" dù MonoStudio không cài ở Program Files. Chi tiết: `.cursor/rules/rule_build_v1.mdc` (mục Extra tools), `docs/MONOSTUDIO_EXTRA_TOOL_SPEC.md`.

---

# MonoStudio 26 — Release v26.5.2

## Highlights

- **Fix**: Đọc version MonoFXSuite thêm fallback từ `install_path.txt` — hiển thị đúng dù cấu trúc thư mục cài khác (vd. không có _internal).

## Changes in this release

- fix: get_extra_tool_installed_version — fallback đọc VERSION từ path trong %LOCALAPPDATA%\\MonoStudio\\install_path.txt (chuẩn hóa _internal → parent)

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.5.1

## Highlights

- **Fix**: Settings → Updates hiển thị đúng phiên bản MonoFXSuite khi cài "Under MonoStudio" (onedir: dùng tools root = parent của _internal).

## Changes in this release

- fix: get_tools_install_root() — đọc VERSION extra tools từ {install}/tools/ (không phải _internal/tools/) để MonoFXSuite version hiện đúng

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.5.0

## Highlights

- **Settings → Updates — trung tâm cập nhật**: MonoStudio + MonoFXSuite (và extra tools) trong một màn; hiển thị phiên bản đang cài, nút Download bản mới từ GitHub. Cài tool khác không thoát app; chỉ cài MonoStudio mới thoát.
- **Install path cho installer khác**: Ghi đường dẫn cài thực tế vào `%LOCALAPPDATA%\MonoStudio\install_path.txt` mỗi lần chạy — installer MonoFXSuite đọc để điền đúng "Under MonoStudio" dù MonoStudio không cài ở Program Files.
- **Docs**: `docs/MONOSTUDIO_EXTRA_TOOL_SPEC.md` (spec tích hợp tool ngoài), build/release note trong RELEASE_NOTES và rule_build.

## Changes in this release

- feat: Settings → Updates — extra repos (MonoFXSuite): version đã cài từ VERSION, Download installer trong app, loading bar đúng hàng, không thoát app khi cài tool khác
- feat: app_paths.write_install_path_for_tools() — ghi install_path.txt cho installer MonoFXSuite
- docs: MONOSTUDIO_EXTRA_TOOL_SPEC (vị trí cài, install_path.txt, Option A/B/C), rule_build + RELEASE_NOTES build note

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.4.0

## Highlights

- **Build & Release docs**: Thêm `docs/BUILD_AND_RELEASE.md` — quick reference build → push → publish; chi tiết trong `.cursor/rules/rule_build_v1.mdc`.
- **Extra tool spec**: Thêm `docs/MONOSTUDIO_EXTRA_TOOL_SPEC.md` — yêu cầu tích hợp cho tool ngoài (vd. MonoFXSuite): vị trí cài, file VERSION, GitHub Release.
- **UI / Settings / Style**: Cập nhật settings dialog, style, update checker.

## Changes in this release

- docs: BUILD_AND_RELEASE.md (build → publish flow)
- docs: MONOSTUDIO_EXTRA_TOOL_SPEC.md (vị trí cài, VERSION, release cho extra tools)
- feat: ui update (settings, style, update_checker)

---

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

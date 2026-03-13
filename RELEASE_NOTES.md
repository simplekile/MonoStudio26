# MonoStudio 26 — Release notes

## Build / release note (khi build & publish)

- **Commit trước khi build**: `build_installer.ps1` mặc định tự commit; dùng `-NoCommit` nếu đã commit tay. Version lấy từ git count + VERSION.
- **Changelog**: Cập nhật `RELEASE_NOTES.md` (version, Highlights, Changes) trước hoặc sau build; `publish_release.ps1` ưu tiên đọc file này.
- **Install path cho extra tools**: Bản cài MonoStudio mỗi lần chạy ghi đường dẫn cài vào `%LOCALAPPDATA%\MonoStudio\install_path.txt` — installer MonoFXSuite (và tool khác) đọc file này để điền đúng "Under MonoStudio" dù MonoStudio không cài ở Program Files. Chi tiết: `.cursor/rules/rule_build_v1.mdc` (mục Extra tools), `docs/MONOSTUDIO_EXTRA_TOOL_SPEC.md`.

---

# MonoStudio 26 — Release v26.8.2

## Highlights

- **Inspector — Department Pipeline mới**: Hiển thị rõ subdepartment theo group Modelling/Rigging/Surfacing…, kèm border focus (sidebar vs Inspector) và nút nhanh tới folder từng department.
- **Inspector — Identity & DCC badges**: Double‑click badge DCC mở thẳng work file mới nhất của DCC đó; hàng nút Asset/Work/Publish folder giúp truy cập thư mục nhanh hơn.
- **Assets/Shots switch mượt hơn**: Khi chuyển giữa Assets và Shots, app chỉ reload UI từ dữ liệu sẵn có; filesystem scan chạy nền qua WorkerManager nên UI không bị khựng.
- **Inspector thumbnail gọn hơn**: Các nút Fill/Fit, Paste, Remove trên thumbnail trong Inspector chỉ hiện khi hover vào vùng thumbnail, không chiếm chỗ khi xem nhanh.

## Changes in this release

- feat: Department Pipeline trong Inspector ẩn hàng department cha dư thừa (Modelling, Rigging, Surfacing…) khi đã có subdepartment; thêm section header nhỏ, đồng bộ typography/hover, và folder icon riêng từng department.
- feat: Department focus: sidebar focus hiển thị border vàng (persistent), click ngay trong Inspector tạo temp focus border xanh; click nền bỏ temp focus và revert về sidebar focus; khi mở app + chọn asset đầu tiên, Inspector tự sync focus với sidebar filter.
- feat: Inspector Identity — double‑click vào DCC badge dùng `_path_for_version()` để mở trực tiếp work file mới nhất cho department + DCC đó.
- feat: Inspector Asset row — nút `Open folder` đổi thành ba nút `Asset folder`, `Work folder`, `Publish folder` (work/publish mở theo department đang focus trong Inspector).
- fix: Assets/Shots context switch dùng `_submit_rescan_task()` (scan nền) thay cho `_rescan_project()` đồng bộ; giữ lại trigger rescan khi Refresh và các luồng khác.
- fix: Inspector preview thumbnail — nút overlay (Fill/Fit, Paste, Remove) chỉ hiện khi hover, xử lý hover ổn định cả khi rê chuột chậm từ khoảng trống bên trái, không bị nháy khi đi qua icon.
- feat: Inspector thumbnail actions — khi bấm Paste hoặc Remove, hiển thị cặp nút Apply (icon square‑check, xanh lá) và Cancel (icon x, đỏ) ở góc phải; chỉ khi bấm Apply mới thực sự paste/xóa thumbnail, Cancel thì giữ nguyên.

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.8.0

## Highlights

- **Sidebar — Departments / Types**: Departments snap trên cùng, list kéo dài xuống sát header Types; Types có danh sách riêng, nằm sát Recent Tasks / footer.
- **Select Departments dialog**: Thêm tabs theo loại (type); mỗi tab chỉ hiển thị những department mà type đó hỗ trợ, đồng bộ với filter ở sidebar.
- **Recent Tasks**: Nút icon `calendar` cạnh nút Settings để ẩn/hiện block Recent Tasks; trạng thái được lưu qua QSettings.

## Changes in this release

- feat: Sidebar filters — Departments list stretch tới vị trí Types; Types + Tags snap phía dưới, mỗi list có scroll riêng.
- feat: Select Departments dialog — per-type tabs, chọn department theo từng type, đồng bộ với filter sidebar.
- feat: Sidebar footer — nút toggle visibility Recent Tasks (icon calendar), lưu `sidebar/recent_tasks_visible` trong QSettings (MonoStudio26).
- style: SidebarFilterList container nền tối hơn sidebar một chút (`#101012`), giữ border-radius 10px.

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.7.2

## Highlights

- **Settings → Updates**: Nút "Check for updates" luôn gọi API mới (bỏ qua cache) — tìm thấy bản release mới ngay, không cần restart app.

## Changes in this release

- fix: check_for_update(skip_cache=True) khi user bấm "Check for updates"; startup check vẫn dùng cache 1h.

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.7.1

## Highlights

- **Settings → Updates**: Nút "Latest" (MonoStudio + extra tools) disable khi đã mới nhất — không bấm nhầm; nút action dài thêm 1/3 (128px).

## Changes in this release

- fix: Nút Download/Latest — setEnabled(False) khi trạng thái Latest (MonoStudio + extra repos).
- style: _UPDATE_ACTION_WIDTH 96 → 128 (dài thêm 1/3).

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.7.0

## Highlights

- **Settings → Updates**: Layout kiểu Windows Update — status (icon + message + "Last checked") bên trái, nút "Check for updates" gọn bên phải; trạng thái tổng cho tất cả app (MonoStudio + extra tools).
- **Last checked**: Lưu và hiển thị thời điểm check gần nhất (kể cả khi mở app hoặc bấm Check); format "Today, 8:25 AM" / "Yesterday, 3:00 PM".
- **Một hàm check chung**: `run_full_update_check()` — dùng cho cả check lúc mở app và nút "Check for updates" (MonoStudio + extra repos).
- **Thêm app mới**: Chỉ cần thêm vào `EXTRA_REPOS` trong `update_checker.py`; UI và logic check tự mở rộng. Doc: `docs/MONOSTUDIO_EXTRA_TOOL_SPEC.md` §5.

## Changes in this release

- feat: Updates tab — status row (icon + message + last checked) trái, button phải; status tổng all apps; icon/màu theo rule MonoStudio.
- feat: Last checked persist (QSettings); hiển thị khi mở tab; startup check cũng ghi last check time.
- refactor: run_full_update_check() trong update_checker — startup + Check button dùng chung; cả hai chạy MonoStudio + fetch_extra_repos.
- docs: EXTRA_REPOS comment + MONOSTUDIO_EXTRA_TOOL_SPEC.md §5 — hướng dẫn thêm nhiều app.

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.6.0

## Highlights

- **Settings → Updates**: Extra tools (MonoFXSuite) giống MonoStudio — so sánh version đang cài với latest từ GitHub; chỉ hiện "Download vX.X.X" khi có bản mới, đã mới nhất thì hiện "Latest".

## Changes in this release

- feat: _apply_extra_repos_ui — dùng is_newer_than(installed, info.version); Download vX.X.X khi update available, Latest khi đã latest

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.5.4

## Highlights

- **Fix**: Settings → Updates hiển thị version MonoFXSuite (và extra tools) ngay khi mở, không cần bấm "Check for updates".

## Changes in this release

- fix: _apply_extra_repos_ui — khi chưa có API data vẫn set version = get_extra_tool_installed_version (không ghi "—")

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.5.3

## Highlights

- **Fix**: Ưu tiên đọc VERSION MonoFXSuite từ thư mục chứa exe đang chạy — Settings → Updates hiển thị đúng version.

## Changes in this release

- fix: get_extra_tool_installed_version — thêm candidate từ sys.executable parent (install root) khi frozen

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

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
- **Sidebar**: Thêm / cập nhật tính năng mới ở sidebar (layout + selectable list pattern thống nhất với rule `selectable_list_pattern_v1`).

## Changes in this release

- docs: BUILD_AND_RELEASE.md (build → publish flow)
- docs: MONOSTUDIO_EXTRA_TOOL_SPEC.md (vị trí cài, VERSION, release cho extra tools)
- feat: sidebar ui update (selectable list pattern, style)

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

# So sánh code: Trước vs Sau khi build plan (Sidebar Compact + Responsive + Gap fix)

So sánh với commit trước (trước khi triển khai plan sidebar compact, responsive panels, và sửa gap).

---

## 1. `main_window.py`

### Layout & Sidebar

| Trước | Sau |
|-------|-----|
| `_main_splitter.addWidget(self._sidebar)` — add trực tiếp Sidebar | `_main_splitter.addWidget(self._sidebar_container)` — add container chứa **QStackedWidget** (Sidebar full **hoặc** SidebarCompact) |
| Không có `setHandleWidth` | `self._main_splitter.setHandleWidth(0)` — không còn khe giữa sidebar và main view |
| Không có container/stack | Tạo `_sidebar_container` (QWidget) + `_sidebar_stack` (QStackedWidget), index 0 = Sidebar, index 1 = SidebarCompact; container có `setMinimumWidth(256)` và khi chuyển compact set 56/56, khi full set 256 |

### Responsive

| Trước | Sau |
|-------|-----|
| Không có logic responsive | `_WIDTH_HIDE_INSPECTOR = 1000`, `_WIDTH_HIDE_SIDEBAR = 720` |
| Không có `_apply_responsive_panels` | Có: width &lt; 720 → dùng SidebarCompact (56px), ẩn inspector; width ≥ 720 → dùng Sidebar full, restore sizes; khi chuyển compact/full cập nhật `_sidebar_container.setMinimumWidth`/`setMaximumWidth` |
| Không lưu sizes để restore | `_main_splitter_sizes_restore`, `_content_splitter_sizes_restore` — lưu sizes khi ẩn panel để restore khi mở rộng lại |

### Signals & sync

| Trước | Sau |
|-------|-----|
| `_top_bar.project_switch_requested` → `_switch_project` | `_sidebar.project_switch_requested` → `_switch_project`; `_top_bar.settings_clicked` → `_open_settings` |
| Chỉ `_sidebar.set_projects` / `set_recent_tasks` | Gọi thêm `_sidebar_compact.set_projects` và `_sidebar_compact.set_recent_tasks` |
| `_on_recent_task_clicked` chỉ `_sidebar.set_current_context(ctx)` | Thêm `_sidebar_compact.set_current_context(ctx)` |

### Compact sidebar wiring

- Kết nối `_sidebar_compact.context_changed` → `lambda ctx: self._sidebar.set_current_context(ctx)` (sync vào full sidebar rồi full sidebar emit).
- Kết nối `_sidebar_compact.context_clicked`, `project_switch_requested`, `recent_task_clicked`, `recent_task_double_clicked`, `clear_recent_tasks_requested` vào cùng handler với full sidebar.

### closeEvent

| Trước | Sau |
|-------|-----|
| `main_sizes = self._main_splitter.sizes()` | `main_sizes = self._main_splitter_sizes_restore if (self._sidebar_stack.currentIndex() == 1) else self._main_splitter.sizes()` — khi đang compact thì lưu bộ sizes “full” đã lưu trước đó |
| Tương tự content_splitter | Dùng `_content_splitter_sizes_restore` khi inspector đang ẩn |

### Import

- Thêm: `SidebarCompact`, `QSizePolicy`.

---

## 2. `sidebar.py`

### Sidebar (full)

| Trước | Sau |
|-------|-----|
| Block 1: **Brand** (logo + MONOS + version) ở top | Block 1: **Project switcher** (QToolButton + dot icon, menu project) ở top |
| Không có `project_switch_requested` | Thêm signal `project_switch_requested`; thêm `set_projects()`, `_project_dot_icon()`, `_show_sidebar_project_menu`, menu popup |
| Block 3: Header "RECENT TASKS" là QLabel | Header là **QPushButton** (`_tasks_header_btn`) — click để toggle ẩn/hiện list |
| Block 4: Footer có nút toggle Recent Tasks + nút Settings | Footer chỉ còn **logo nhỏ + MONOS + version** (bỏ nút toggle, bỏ Settings) |
| Collapse Recent Tasks = ẩn cả block | Collapse = chỉ ẩn list, header vẫn hiện; dùng `_tasks_block_h_expanded` / `_tasks_block_h_collapsed` cho fixed height |

### SidebarCompact (mới)

- **Class mới** `SidebarCompact(QWidget)`:
  - Rộng cố định 56px.
  - Layout dọc: project (icon dot) → separator → scope (Projects / Shots / Assets) → separator → Inbox / Project Guide / Outbox → separator → Recent tasks (icon) → stretch → separator → footer (logo).
  - Signals: `context_changed`, `context_clicked`, `project_switch_requested`, `recent_task_clicked`, `recent_task_double_clicked`, `clear_recent_tasks_requested`.
  - API: `set_projects()`, `set_current_context()`, `current_context()`, `set_recent_tasks()`, `set_filter_source(filters)`.
  - Recent tasks: click icon → popup list (cùng delegate với full sidebar).
- Hàm helper: `_sep_line()` tạo separator cho compact.

---

## 3. `style.py`

| Trước | Sau |
|-------|-----|
| Chỉ có QSplitter::handle chung (width 1px, nền xám) | Thêm **QSplitter#MainSplitter::handle**: `width: 0`, `min-width: 0`, `max-width: 0`, `background: transparent`, `border: none` (hỗ trợ không hiện khe) |
| Không có style cho project switch / footer sidebar | **SidebarProjectSwitch**, **SidebarBottom** (#1e1e21), **SidebarFooterName**, **SidebarFooterVersion** |
| Không có style cho header Recent Tasks dạng button | **SidebarRecentTasksHeaderButton** (transparent, hover đổi màu chữ) |
| Không có SidebarCompact | **#SidebarCompact**, **#SidebarCompactProjectSwitch**, **#SidebarCompactScopeButton** / **FooterNavButton** / **RecentTasksButton**, **#SidebarCompactRecentTasksPopup** |

---

## 4. Các thay đổi khác trong diff (không thuộc plan sidebar)

Trong `git diff` còn có các phần **không** thuộc plan sidebar/responsive/gap, ví dụ:

- **Watcher**: `_watcher_manually_disabled`, `_on_watcher_toggled`, `_top_bar.watcher_toggled`, logic tắt watcher (release handle), `_watcher_paths_for_asset`, `_update_fs_watcher_paths` không watch trực tiếp asset/shot folder.
- **Rename asset**: `prepare_work_file_renames`, `work_file_renames`, thông báo “pause file watcher” trước khi rename, xử lý winerror 5 (access denied), gọi `_update_fs_watcher_paths` sau rename.
- Import: `time`, `prepare_work_file_renames`.

Những phần trên là tính năng/sửa lỗi riêng, không nằm trong “build plan” sidebar compact/responsive/gap.

---

## Tóm tắt phần “plan”

1. **Sidebar Compact**: Widget 56px chỉ icon, stack với Sidebar full; resize &lt; 720px thì chuyển sang compact.
2. **Responsive**: Ẩn inspector khi hẹp, chuyển sidebar sang compact khi rất hẹp; lưu/restore splitter sizes.
3. **Gap**: `setHandleWidth(0)` + style handle MainSplitter 0 width/transparent + container min/max theo chế độ.
4. **Sidebar full**: Project switcher lên top, logo + MONOS + version xuống footer; Recent Tasks toggle bằng click vào header, collapse chỉ ẩn list.
5. **Top bar**: Settings lên top bar; project switch chỉ từ sidebar (full/compact).

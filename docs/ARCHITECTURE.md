# MONOS — Architecture (v1)

Tài liệu canonical về phân tầng, luồng dữ liệu, và quy tắc dependency cho MonoStudio26 desktop app (PySide6).

**Triết lý UI:** `docs/ui_ux_spec_v1.md` — filesystem là sự thật; Browse-first, Action-second.

**Kế hoạch refactor Main View:** `.cursor/rules/plan_main_view_engine_v2.mdc`

---

## 1. Tổng quan

MonoStudio là **desktop pipeline tool local-first**: quét thư mục project, hiển thị asset/shot, mở DCC, inbox/delivery, review video.

```
app.py
  └── QApplication + apply_dark_theme()
        └── MainWindow (composition root)
              ├── AppState, WorkerManager, ThumbnailManager
              ├── Sidebar / NavRail / TopBar
              ├── QStackedWidget pages (MainView, Inbox, Schedule, …)
              └── InspectorPanel
```

**Đánh giá hiện tại (2026):** Ý tưởng phân tầng **rõ trên spec**; thực thi **chưa đồng đều** — vài god file (~5k–10k dòng) và dependency ngược `core` ↔ `ui_qt`.

---

## 2. Phân tầng mục tiêu

```
┌─────────────────────────────────────────────────────────────────┐
│ L0  Entry          app.py                                         │
├─────────────────────────────────────────────────────────────────┤
│ L1  Shell (Widgets) MainWindow — wire signals, routing            │
├─────────────────────────────────────────────────────────────────┤
│ L2  Feature panes  Inbox, Schedule, Inspector, … (Widgets)      │
│                    Main View host (Widgets wrapper)               │
├─────────────────────────────────────────────────────────────────┤
│ L2b Main View UI   Qt Quick QML — grid + list (**bắt buộc**)      │
│                    QQuickWidget ← PipelinePresentationModel       │
├─────────────────────────────────────────────────────────────────┤
│ L3  Presentation   Snapshot, model roles, QML bridge, providers │
├─────────────────────────────────────────────────────────────────┤
│ L4  App state      AppState, PipelineSelectionStore, filters*     │
├─────────────────────────────────────────────────────────────────┤
│ L5  App services   Health, DCC status, thumbnail facade           │
├─────────────────────────────────────────────────────────────────┤
│ L6  Domain         monostudio/core — models, scan, registries    │
└─────────────────────────────────────────────────────────────────┘
```

**UI stack:** Hybrid **Widgets + QML**. Shell/Inspector/dialog = Widgets; **Main View pipeline browser = QML** (đích cuối, không tùy chọn).

\* Filter department/type hiện duplicate — xem mục 6.

---

## 3. Package map

| Package / path | Trách nhiệm | Qt? |
|----------------|-------------|-----|
| `app.py` | Bootstrap, splash, single-instance, deep link | Có |
| `monostudio/core/` | Domain models, `fs_reader`, registries, DCC adapters, inbox/delivery readers, schedule logic | Không (mục tiêu) |
| `monostudio/ui_qt/` | Widgets UI, `style.py`, workers, thumbnails | Có (Widgets) |
| `monostudio/ui_qt/qml/pipeline/` | **Main View QML** — grid, list, cards, badges | QML (Qt Quick) |
| `monostudio/ui_qt/dialog_tier/` | Design system dialog L1/L2 (golden reference) | Có (Widgets) |
| `monostudio/plugins/` | Extension tùy chọn (hiện: pomodoro) | Có |
| `monostudio_data/` | Icons, pipeline JSON templates | — |
| `docs/` | Spec sản phẩm + **ARCHITECTURE.md** | — |
| `.cursor/rules/` | Plan triển khai + rule UI enforce | — |

---

## 4. Luồng dữ liệu

### 4.1 Scan → UI (happy path)

```
Disk (project folder)
    ↓
build_project_index()          [core/fs_reader.py]
    → Asset / Shot (+ Department, DccWorkState)
    ↓
WorkerManager ("filesystem_scan" | "incremental_scan")
    ↓
AppState.update_assets() / update_shots()   [debounce ~80ms, diff signals]
    ↓
MainWindow → MainView.set_items() / model bind
    ↓
MainWindow → MainView host / PipelineQmlBridge
    ↓
ViewItem + PipelinePresentationModel (QAbstractListModel)
    ↓
PipelineRowSnapshot roles → QML GridView / ListView
    (Widget delegates = legacy, deprecate sau cutover)
```

### 4.2 User intent

```
View emits intent signal (open_requested, delete_requested, …)
    ↓
MainWindow slot (hoặc AppController cho DCC open)
    ↓
core I/O (rename, trash, launch DCC, …)
    ↓
AppState / rescan / invalidate
    ↓
Views react qua signals
```

**Quy tắc:** Widget **không** gọi `shutil`, `subprocess` explorer, hay DCC trực tiếp — dùng `core/*` hoặc coordinator trong MainWindow.

### 4.3 Thumbnail (song song)

```
ThumbnailManager.request_thumbnail()
    → cache hit: QPixmap ngay
    → miss: WorkerManager decode → taskFinished → AppState.notify_thumbnail_ready
MainView prefetch visible rows (chunked)
```

---

## 5. Shell UI (3 pane)

Theo `ui_ux_spec_v1.md`:

| Pane | ~% | Vai trò |
|------|-----|---------|
| Sidebar + NavRail | 15% | Context (Assets/Shots/Inbox…), filter dept/type |
| Main View | 60% | Grid / List pipeline browser — **QML** (`plan_main_view_engine_v2`) |
| Inspector | 25% | Info selected entity; ẩn khi không chọn |

**MainView** emit **intent signals**; **MainWindow** xử lý side effects. Không đảo chiều (MainWindow không paint card).

---

## 6. State management

### 6.1 AppState (`ui_qt/app_state.py`)

- SSOT cho `assets`, `shots`, selection id, filter dept/type (một phần)
- Diff-based signals: `assetsChanged`, `shotsChanged`, `selectionChanged`, `thumbnailsChanged`
- Workers **chỉ** cập nhật AppState; không chạm widget

### 6.2 PipelineSelectionStore (`ui_qt/pipeline_selection.py`)

- Multi-select grid/list theo `set[Path]`
- Đang tích hợp dần — chưa thay hoàn toàn selection trong MainView

### 6.3 Duplicate filter state (vi phạm — cần gom)

| Nơi lưu | Field |
|---------|--------|
| `MainWindow` | `current_department`, `current_type` |
| `AppController` | `current_department` |
| `AppState` | `_filter_department`, `_filter_type` |
| `Sidebar` filters | `filters().current_department()` |

Sync qua signal/slot — dễ lệch. **Mục tiêu:** một `FilterState` hoặc mở rộng AppState làm SSOT duy nhất.

---

## 7. Async & workers

| Component | Vai trò |
|-----------|---------|
| `WorkerManager` | `QThreadPool`, `WorkerTask`, `taskFinished` trên main thread |
| Categories | `filesystem_scan`, `thumbnail_load`, `metadata_read`, … |
| `ThumbnailManager` | LRU memory + disk cache; decode off UI thread |

**Cấm:** decode ảnh lớn, scan FS nặng, ffmpeg trong `paint()` hoặc slot UI đồng bộ (trừ cache hit O(1)).

---

## 8. Design system

| Concern | Module / rule |
|---------|----------------|
| Triết lý UI | `docs/ui_ux_spec_v1.md`, `ui_design.md` |
| Dialog golden reference | `scripts/test_dialog_tiers.py`, `plan_dialog_tier_golden_reference_v1.mdc` |
| Tiers & tokens | `monostudio.md` — Tier 1/2/3 |
| Global QSS, palette | `ui_qt/style.py` — `apply_dark_theme()` |
| Dialog L1/L2 | `ui_qt/dialog_tier/` — `rule_dialog_design_system_v1.mdc` |
| Token map (Widgets ↔ QML) | `docs/DESIGN_SYSTEM_MAP.md` (plan v2 §4.4) |
| Main View QML theme | `ui_qt/qml/pipeline/PipelineTheme.qml` — sync `MONOS_COLORS` |
| Dialog legacy | `MonosDialog` + `dialog_ui_v1.mdc` |
| Tooltip / scrollbar | `tooltip_qss_v1.mdc`, `scrollbar_qss_v1.mdc` |
| Popover position | `ui_qt/popup_position.py` — `rule_popover_position_v1.mdc` |
| Open folder | `core/shell_open.py` — `rule_open_folder_shell_v1.mdc` |
| Typography | `typography_font_stack_v1.mdc` — Inter + JetBrains Mono |

**Design spine (trước QML skin):** Dialog tier FROZEN → `DESIGN_SYSTEM_MAP.md` → harness `test_pipeline_qml.py` → QML grid. Xem `plan_main_view_engine_v2.mdc` §4.4.

**Không** inline QSS rải rác cho pattern đã có trong `style.py` hoặc `dialog_tier`.  
**QML Main View:** dùng `PipelineTheme` singleton — không hardcode hex lẻ trong `.qml`.

---

## 9. Dependency rules (bắt buộc)

### MUST

| Rule | Lý do |
|------|-------|
| `core/` không `import monostudio.ui_qt` | Domain độc lập presentation |
| View không chứa business logic nặng trong `paint()` | UI thread budget |
| Intent lên, state xuống | Testable, một chiều |
| Utility một mục đích một module | `shell_open`, `popup_position` |
| Dialog mới dùng `dialog_tier` | Visual consistency |
| Main View grid/list dùng **QML** | `plan_main_view_engine_v2` — bắt buộc đích cuối |
| Private `_foo` không import cross-file | Tạo public API trong service module |

### MUST NOT

| Rule | Lý do |
|------|-------|
| God file mới > 1500 dòng feature / 3000 shell | Maintainability |
| `QDesktopServices` / `explorer` trực tiếp cho folder | Dùng `shell_open` |
| `beginResetModel()` khi chỉ đổi 1 row | Repaint storm |
| Thêm feature badge vào Widget delegate Main View | Chỉ QML sau Sprint 2 |
| Web/Electron cho Main View desktop | Chi phí >> lợi ích |

---

## 10. God objects (theo dõi — 2026-03)

| File | Dòng | Ghi chú |
|------|-----:|---------|
| `ui_qt/main_window.py` | ~10,076 | Orchestrator — tách coordinator |
| `ui_qt/main_view.py` | ~8,503 | View + delegate + health — plan v2 |
| `ui_qt/video_preview_dialog.py` | ~7,444 | Review player |
| `ui_qt/style.py` | ~6,305 | Theme + widgets — tách dần |
| `ui_qt/schedule_timeline_widget.py` | ~6,046 | Schedule |
| `ui_qt/inspector.py` | ~5,599 | Inspector blocks |
| `ui_qt/sidebar.py` | ~4,566 | Nav + filters |

**File mẫu đúng kích thước:** `app_state.py` (~241), `app.py` (~213), `worker_manager.py`.

---

## 11. Dependency violations map

### 11.1 `core` → `ui_qt` (ngược tầng — sửa dần)

| File core | Import ui_qt | Hướng sửa |
|-----------|--------------|-----------|
| `core/djv_launch.py` | `video_preview_settings`, `video_preview_context` | Đọc settings qua `core` config; request DTO thuần dataclass |
| `core/review_media.py` | `video_preview_context`, `thumbnails` | Tách `core/review_types.py`; UI map sang context |
| `core/dcc_fusion.py` | `comp_saver_preflight` | Callback / protocol inject từ UI khi launch |
| `core/sequence_proxy.py` | `sequence_preview_decode` | Move decode vào `core/sequence_preview.py` hoặc worker-only bridge |
| `core/ocio_display.py` | `ocio_preview_settings` | `OcioPreviewState` dataclass trong `core/` |

### 11.2 `ui_qt` → `main_view` private API (hub file — gom service)

| Consumer | Import từ `main_view` | Nên chuyển sang |
|----------|----------------------|-----------------|
| `inspector.py` | `assess_view_item_health`, `_item_active_dcc`, `_department_for_item`, … | `pipeline_item_health.py` hoặc `core/project_health.py` |
| `pipeline_list_delegate.py` | `assess_view_item_health`, `_item_active_dcc` | Cùng service + `PipelineRowSnapshot` |
| `pipeline_list_hit.py` | `_dcc_ids_for_item`, `assess_view_item_health` | `pipeline_dcc_status.py` |
| `pipeline_row_paint.py` | `_dcc_ids_for_item`, … | Snapshot / service |
| `app_controller.py` | `_item_active_dcc`, `_open_metadata_path` | `pipeline_active_dcc.py`, `core` path helper |
| `item_health_dialog.py` | `HealthIssue`, `assess_view_item_health`, … | `pipeline_item_health.py` |
| `pipeline_list_view.py` | `_card_bg_colors_for_browser_mode` | `pipeline_grid_chrome.py` (theme tokens) |

### 11.3 Presentation bypass AppState

| Hiện tượng | Vị trí | Mục tiêu |
|------------|--------|----------|
| Health/DCC tính trong `paint()` | `_GridCardDelegate`, `PipelineListRowDelegate` | `PipelineRowSnapshot` precompute |
| Schedule/review badge per paint | `main_view` delegate | Snapshot + invalidate facet |
| Filter 3 nơi | MainWindow, AppController, Sidebar | Single `FilterState` |

### 11.4 Đã sạch (tham chiếu)

- `core/models.py`, `core/fs_reader.py` — không Qt
- `core/shell_open.py` — Windows COM, UI gọi qua API
- `ui_qt/popup_position.py` — layout only
- `ui_qt/worker_manager.py` — không widget
- `ui_qt/pipeline_selection.py` — selection store thuần

---

## 12. Module đề xuất (tách từ main_view)

| Module mới | Nội dung di chuyển |
|------------|-------------------|
| `pipeline_item_health.py` | `ItemHealth`, `assess_view_item_health`, `HealthIssue` |
| `pipeline_active_dcc.py` | `_item_active_dcc`, open.json read |
| `pipeline_dcc_status.py` | `resolve_dcc_status`, badge list builder |
| `pipeline_snapshot.py` | `PipelineRowSnapshot` + builder + store |
| `pipeline_presentation_model.py` | QAbstractListModel cho QML |
| `pipeline_qml_bridge.py` | QQuickWidget host + intent signals |
| `ui_qt/qml/pipeline/` | QML components |
| `pipeline_view_host.py` | Widget shell Main View |

Chi tiết: `plan_main_view_engine_v2.mdc`.

---

## 13. Lộ trình chuẩn hóa (tóm tắt)

| Phase | Việc | Doc |
|-------|------|-----|
| **D** | Design spine: dialog anchor, token map, harness | `plan_main_view_engine_v2.mdc` §4.4, `DESIGN_SYSTEM_MAP.md` |
| **A** | `PipelineRowSnapshot` + services health/dcc | `plan_main_view_engine_v2.mdc` |
| **A′** | QML grid + list + bridge (**bắt buộc**) | Sprint 2–5 trong plan v2 |
| **B** | Gom `FilterState`; wire `PipelineSelectionStore` | Plan này §6 |
| **C** | Tách `MainWindow` → coordinators | `ProjectCoordinator`, `ScanCoordinator` |
| **D** | Đảo `core`→`ui_qt` violations | §11.1 |
| **E** | Cap file size trong review | §10 |

---

## 14. Tài liệu liên quan

| Tài liệu | Nội dung |
|----------|----------|
| `docs/ui_ux_spec_v1.md` | Triết lý UI, 3-pane, sidebar/main/inspector |
| `.cursor/rules/plan_main_view_engine_v2.mdc` | Rebuild Main View — design spine + **QML bắt buộc** + snapshot |
| `docs/DESIGN_SYSTEM_MAP.md` | Token Widgets ↔ dialog_tier ↔ QML (Phase D) |
| `scripts/test_dialog_tiers.py` | Dialog golden harness |
| `.cursor/rules/plan_list_view_v2.mdc` | Pipeline List Row (đã migrate) |
| `.cursor/rules/rule_dialog_design_system_v1.mdc` | Dialog mới |
| `docs/BUILD_AND_RELEASE.md` | Build / release |

---

## 15. Checklist PR (architecture)

- [ ] Không thêm `core` → `ui_qt` import mới
- [ ] Không import `_private` từ `main_view.py` — thêm public API service
- [ ] Không business logic mới trong delegate `paint()`
- [ ] Intent signal cho action user-facing mới
- [ ] QSS/tooltip/scrollbar qua `style.py` global
- [ ] Dialog create flow dùng `dialog_tier` nếu L1/L2
- [ ] Main View feature mới → QML (`ui_qt/qml/pipeline/`), không Widget delegate
- [ ] Dialog mới L1/L2 → compose `dialog_tier`; so `test_dialog_tiers.py`
- [ ] File mới feature ≤ 1500 dòng hoặc justify trong PR

---

*Cập nhật: 2026-03 — QML bắt buộc cho Main View; handoff review performance & layering.*

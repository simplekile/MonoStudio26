# MONOS — Design System Map (v1)

Token bridge: **Widgets (`style.py`) · Dialog (`dialog_tier/T`) · QML (`PipelineTheme.qml`)**.

**Plan:** `.cursor/rules/plan_main_view_engine_v2.mdc` §4.4  
**Verify:** `python scripts/test_pipeline_qml.py --verify-theme`

---

## 1. Nguyên tắc

| Rule | Detail |
|------|--------|
| Một nguồn sự thật runtime | `MONOS_COLORS` trong `style.py` cho app shell |
| Dialog | `T` tokens từ `dialog_tier` (derived + surfaces) |
| QML Main View | `PipelineTheme.qml` — **hex phải khớp** `build_pipeline_theme_map()` |
| Không hardcode | Không thêm hex lẻ trong QML/dialog mới nếu token đã có |

---

## 2. Surface & layout (app shell)

| Semantic | MONOS_COLORS key | PipelineTheme (QML) | Dialog `T` / surfaces |
|----------|------------------|---------------------|------------------------|
| App background | `app_bg` `#09090b` | `appBg` | `SURFACE_APP` |
| Main view / browser | `content_bg` `#151618` | `contentBg` | — |
| Panel / dialog shell | `panel` `#18181b` | `panel` | `SURFACE_DIALOG` |
| Chrome (sidebar/topbar) | `chrome_bg` `#181a1d` | `chromeBg` | — |
| Card (grid) | `card_bg` `#191b1e` | `cardBg` | `SURFACE_CARD` |
| Card hover | `card_hover` `#1d1f23` | `cardHover` | — |
| Border default | `border` `#27272a` | `cardBorder` | chroma borders |

---

## 3. Typography

| Role | Size | Weight | Font | MONOS / QML |
|------|------|--------|------|-------------|
| Card name (Tier 1) | 13px | 600 | Inter | `nameSize` |
| Meta / header hint | 11px | 500 | Inter | `metaSize` |
| Status pill | 10px | 500–600 | Inter | `statusSize` |
| Path / ID | 12–13px | 500 | JetBrains Mono | `fontMono` |

Rule: `typography_font_stack_v1.mdc`

---

## 4. Text colors

| Role | MONOS_COLORS | PipelineTheme |
|------|--------------|---------------|
| Body primary | `text_primary` `#cccccc` | `textPrimary` |
| Selected name | `text_primary_selected` `#fafafa` | `textPrimarySelected` |
| Label | `text_label` `#a1a1aa` | `textLabel` |
| Meta | `text_meta` `#71717a` | `textMeta` |

---

## 5. Accent & semantic

| Meaning | MONOS_COLORS | PipelineTheme | UI usage |
|---------|--------------|---------------|----------|
| Primary / selection | `blue_600` `#2563eb` | `blue600` | Selected card border, CTA |
| Hover accent | `blue_500` `#3b82f6` | `blue500` | Pill hover |
| Link / highlight | `blue_400` `#60a5fa` | `blue400` | Nav active |
| Approved | `emerald_500` `#10b981` | `emerald500` | Status |
| In progress | `amber_500` `#f59e0b` | `amber500` | Status |
| Blocked | `red_500` `#ef4444` | `red500` | Status / alert |
| Waiting | `waiting` `#71717a` | `waiting` | Status |

---

## 6. Radius & spacing (8px grid)

| Token | px | Widgets / QML |
|-------|-----|----------------|
| Card radius | 12 | `radiusCard` — grid card, dialog L2 |
| Pill / status chip | 8 | `radiusPill` |
| Small chip | 4 | `radiusChip` |
| Card padding | 16 | `PipelineCard.qml` margins |
| Thumb badge inset | 12 | §6 plan |
| Grid gap | 16 | `PipelineGridView.cardGap` |

Dialog metrics: `T_METRICS` (`radius_l1/l2` = 16, `radius_sm` = 8, `form_spacing_y` = 24).

---

## 7. Motion

| Animation | Duration | Easing | Where |
|-----------|----------|--------|-------|
| Thumb fade-in | 120ms | OutCubic | `ThumbImage.qml` |
| Hover border/bg | 150ms | default | `PipelineCard.qml` |
| Selection border | 100ms | — | plan §7 |

---

## 8. Dialog tier ↔ app

| Dialog | Harness | Production path |
|--------|---------|-----------------|
| L1 New Project | `NewProjectTier1Demo` | `test_dialog_tiers.py` |
| L2 Create Asset/Shot | `NewAssetTier2Demo` | port to `create_entry_dialogs.py` |
| DCC picker | `DccPickerDemo` | `dialog_tier/dcc_picker.py` |

Dialog **FROZEN** — không đổi golden để khớp màn cũ; nâng màn cũ lên golden.

---

## 9. QML module map

| File | Role |
|------|------|
| `qml/Monos/Pipeline/PipelineTheme.qml` | Singleton colors + metrics |
| `PipelineCard.qml` | Grid card §6 |
| `PipelineGridView.qml` | GridView delegate |
| `ThumbImage.qml` | Thumb + fade |
| `BadgeChip.qml`, `StatusPill.qml`, `DccStack.qml` | Primitives |
| `PipelineGridHarness.qml` | Harness root |

Python: `pipeline_qml_theme.py` — `configure_pipeline_qml_engine()`, `build_pipeline_theme_map()`.

Harness: `scripts/test_pipeline_qml.py`

---

## 10. Page archetype → token source

| Archetype | Pages | Token source |
|-----------|-------|--------------|
| A Shell | Sidebar, TopBar, Inspector frame | `style.py` QSS |
| B Browser | Main View grid/list | `PipelineTheme.qml` |
| C Data pane | Inspector blocks, Settings | `MetadataCard`, `SURFACE_CARD` |
| D Explorer | Inbox, Project Guide | C + list density |

---

## 11. Checklist khi thêm token mới

1. Thêm vào `MONOS_COLORS` (nếu app-wide)
2. Mirror trong `build_pipeline_theme_map()` + `PipelineTheme.qml`
3. Chạy `python scripts/test_pipeline_qml.py --verify-theme`
4. Ghi một dòng vào bảng §2–§5 trong file này
5. Nếu dialog cần: promote vào `T` via `surfaces.py` / `reference.py` (design review)

---

*Phase D deliverable — cập nhật khi token thay đổi.*

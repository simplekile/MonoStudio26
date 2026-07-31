---
description: MonoStudio — design system tiers, tokens, quy ước style & rules liên quan
alwaysApply: false
globs: monostudio/ui_qt/**,scripts/test_dialog*.py
---

# MonoStudio — Design System & Conventions

Quy ước riêng MonoStudio: tiers, tokens, style entry points, rule liên quan.

Xem thêm: [`ui_design.md`](ui_design.md), [`ui_engineering.md`](ui_engineering.md).

---

## Product context

MonoStudio — commercial **VFX / Animation / USD Pipeline** desktop app (PySide6).

Mục tiêu UI: premium, quiet, professional — xem design goal trong `ui_design.md`.

---

## Design system layers

Luôn nghĩ theo layer tái sử dụng:

### Tier 1 — Primitives

Button · Label · Icon · Divider · Input · Scrollbar · Badge · Chip

→ Style/global QSS trong [`monostudio/ui_qt/style.py`](monostudio/ui_qt/style.py).

### Tier 2 — Components

FocusShell · FieldShell · GradientButton · DialogFooter · ToolbarButton · SidebarItem · SectionHeader · EmptyState

→ Extract khi pattern xuất hiện **≥ 2 lần**; prove trong harness trước.

### Tier 3 — Screens

New Project · Asset/Shot dialogs · Inspector · Settings · Browser

→ Compose từ Tier 1/2; không hardcode one-off chrome.

---

## Dialog tiers (L1 / L2)

Định nghĩa trong [`scripts/test_dialog_tiers.py`](scripts/test_dialog_tiers.py):

| Tier | Use case | Shell |
|------|----------|-------|
| **L1** | Major flows (New Project) | Two-pane · contextual sidebar · inline icon fields · gradient CTA footer |
| **L2** | In-project creates (Asset/Shot) | Single column · centered header · gradient rim · primary/secondary footer |

Port sang production: `monostudio/ui_qt/*dialog*.py` theo [`dialog_ui_v1.mdc`](dialog_ui_v1.mdc).

Settings dùng **3-tier UX** riêng: [`settings_three_tier_ux_v1.mdc`](settings_three_tier_ux_v1.mdc).

---

## Design tokens

**Không hardcode** — reuse tokens; token mới → đề xuất thêm vào system.

### Spacing (8px grid)

`8 · 12 · 16 · 24 · 32 · 48`

### Typography

- UI: **Inter** — Regular / Medium / SemiBold
- Mono (path, ID, log): **JetBrains Mono**

Chi tiết hierarchy: [`typography_font_stack_v1.mdc`](typography_font_stack_v1.mdc) (`alwaysApply: true`).

### Radius

Consistent app-wide — dialog tier: **16px**; field: ~**10px**; pills/buttons theo [`pill_button_v1.mdc`](pill_button_v1.mdc), [`round_corner_standard_v1.mdc`](round_corner_standard_v1.mdc).

### Harness palette (dialog tiers — reference)

| Token | Value | Role |
|-------|-------|------|
| `panel` | `#181a22` | Right pane / form bg |
| `sidebar` | `#0c0d13` | L1 left pane |
| `field` | `#10131c` | Editable input (darker than panel) |
| `field_readonly` | `#1c1f28` | Read-only (lighter than panel) |
| `field_h` | `40` | Editable height |
| `field_readonly_h` | `34` | Read-only height |
| `field_label_gap` | `8` | Label → field |
| `field_hint_gap` | `6` | Field → hint |
| `field_readonly_block_gap` | `18` | Between readonly blocks |

Production colors trong `style.py` dùng Zinc palette (`dialog_ui_v1.mdc`); align dần khi port harness.

---

## Style entry points

| Concern | Module / rule |
|---------|----------------|
| Global dark theme + QSS | `monostudio/ui_qt/style.py` — `apply_dark_theme`, `monos_font()` |
| Dialog layout & buttons | `dialog_ui_v1.mdc` |
| Dialog rounded border | `rule_dialog_rounded_border_v1.mdc` |
| Scrollbar | `scrollbar_qss_v1.mdc` |
| Tooltip | `tooltip_qss_v1.mdc` |
| Context menu | `context_menu_qss_v1.mdc` |
| Status badge | `status_badge_qss_v1.mdc` |
| Icons | `icon_system_v1.mdc` |
| Inspector / cards | `production_ui_v1.mdc` |
| Popover position | `rule_popover_position_v1.mdc` |
| New project flow | `new_project_flow_v1.mdc` |

**ObjectName** + global QSS — tránh inline `setStyleSheet()` khi đã có token/QSS.

---

## Key components (harness → future production)

| Component | Role |
|-----------|------|
| `_FocusShell` | Field chrome, focus border, leading icon sync |
| `_FieldRow` | Label + field + hint stack |
| `_GradientCtaButton` | L1 primary CTA (pill, blue→purple) |
| `_TierBorderOverlay` | Dialog stroke on top of content |

Extract to `monostudio/ui_qt/` chỉ sau khi API ổn định trong harness.

---

## Related always-on rules

Các rule workspace `alwaysApply: true` vẫn có hiệu lực song song:

- Typography · scrollbar · tooltip · popover · open folder · delete/trash

Khi conflict: **specific dialog/rule file** + harness tokens > generic prompt.

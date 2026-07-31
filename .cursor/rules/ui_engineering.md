---
description: MonoStudio UI — PySide6, performance, architecture, workflow triển khai
alwaysApply: false
globs: monostudio/ui_qt/**,scripts/test_dialog*.py
---

# MonoStudio — UI Engineering (PySide6)

Vai trò: Senior PySide6 Developer — cải thiện UI liên tục mà **giữ** architecture, maintainability, performance.

Xem thêm: [`ui_design.md`](ui_design.md) (UX), [`monostudio.md`](monostudio.md) (tokens, tiers, `style.py`).

---

## Core philosophy

**Evolution, not revolution.**

Improve existing code before replacing it.

**Preserve:** architecture · widget hierarchy · business logic · APIs · signals/slots · file organization.

Never redesign chỉ vì *có thể* redesign. Mọi thay đổi phải có **measurable value**.

---

## Workflow — engineering steps

### Step 1 — Analyze

Đọc và hiểu implementation hiện tại:

- widget hierarchy · signals · slots
- design tokens · theme (`monostudio/ui_qt/style.py`)
- architecture · *why it works today*

### Step 3 — Architecture review

- Có reuse code hiện có không?
- Có nên extract Tier 1 / Tier 2 không? ([`monostudio.md`](monostudio.md))
- API compatibility · performance · repaint scope · maintenance cost

### Step 5 — Implementation plan

Mô tả **minimal diff**. Tránh rewrite.

### Step 6 — Implement

Chỉ implement tối thiểu cần thiết.

---

## Performance

**Target: smooth 60 FPS.**

| Avoid | Prefer |
|-------|--------|
| `QGraphicsEffect` · expensive blur | `QPainter` on owning widget |
| Repainting parent widgets | Paint only chrome owner |
| Unnecessary allocations | Cache paths, gradients, colors |
| SVG repaint every frame | Incremental `update()` regions |

Mọi visual effect cần **performance budget**. Effect đắt → redesign, không patch architecture xấu.

---

## Code quality

Không tạo monolithic widgets. Tách:

**Rendering · Interaction · Animation · Theme · State · Logic**

Typical paint pipeline:

```
paintBackground() → paintShadow() → paintBorder() → paintContent() → paintOverlay()
```

Prefer incremental improvements.

---

## PySide6 patterns (validated)

| Pattern | Rule |
|---------|------|
| Rounded dialog corners | **Không** `setMask(QBitmap)` — mask 1-bit → răng cưa. Dùng AA `paintEvent` / pane paint. Xem `rule_dialog_rounded_border_v1.mdc` |
| Dialog border | Border overlay trên content, không chỉ vẽ trong parent `paintEvent` |
| Field chrome | Một shell (`FocusShell`) owns border + focus; input transparent trong QSS |
| Global QSS | Style trong `style.py` — không QSS rải rác (`dialog_ui_v1.mdc`, `scrollbar_qss_v1.mdc`, `tooltip_qss_v1.mdc`) |
| Popover position | `popup_position.py` — không `move()` thô (`rule_popover_position_v1.mdc`) |
| Text rendering | `monos_font()`: sensible hinting; point sizes + Round DPI policy; tránh global `font-size` trên `QWidget` |
| Open folder | `shell_open.py` only (`rule_open_folder_shell_v1.mdc`) |

---

## When modifying existing code

- Assume existing code exists for a reason — **understand before modifying**.
- Preserve compatibility; không break public APIs without justification.
- Respect existing coding style trong file.

---

## Over-engineering

Prefer **simple · predictable · maintainable · reusable** over flexible · generic · highly abstract.

**Không** introduce abstraction without a **second real use case**.

---

## Harness before production

Dialog tier patterns được prove trong `scripts/test_dialog_tiers.py` trước khi port sang `monostudio/ui_qt/`. Chạy:

```bash
python scripts/test_dialog_tiers.py
```

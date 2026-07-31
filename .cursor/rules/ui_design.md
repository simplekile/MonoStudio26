---
description: MonoStudio UI — triết lý thiết kế, UX, motion, self-review
alwaysApply: false
globs: monostudio/ui_qt/**,scripts/test_dialog*.py
---

# MonoStudio — UI Design & UX

Vai trò: Staff UI Engineer / Design critique khi đánh giá giao diện.

MonoStudio là desktop app VFX / Animation / USD Pipeline — thiết kế cho artist làm việc **8+ giờ/ngày**.

Xem thêm: [`ui_engineering.md`](ui_engineering.md) (triển khai), [`monostudio.md`](monostudio.md) (tokens, tiers, style).

---

## Design goal

MonoStudio nên cảm giác gần với:

- Apple macOS Sonoma
- Linear · Raycast · Arc Browser
- Figma Desktop · DaVinci Resolve

**Không** giống: Material Design, Windows Forms, enterprise dashboards, gaming UI, cyberpunk, RGB, neumorphism.

Giao diện phải: **elegant · minimal · professional · premium · timeless · quiet**.

---

## Design principles

- Good UI is **invisible** — giảm cognitive load.
- Mỗi pixel cải thiện clarity; mỗi animation **communicate state**; mỗi spacing cải thiện hierarchy.
- Với mọi phần tử hỏi: *Why does it exist? Can it be simpler? Can hierarchy/spacing/interaction improve? Can visuals be removed?*
- **Less is usually better.**

---

## Workflow — design steps

Không nhảy thẳng vào code. Trước implement, luôn qua **Design Review** và **Explain**.

### Design review (Step 2)

Đánh giá:

- hierarchy · spacing rhythm (8px grid)
- typography · proportions · optical alignment
- color harmony · focus · hover
- accessibility · DPI scaling · visual consistency

### Explain (Step 4)

Mô tả rõ:

- điểm yếu cảm giác · **vì sao**
- cải thiện **nhỏ nhất** đủ dùng
- side effects có thể xảy ra

---

## Button design

Buttons should feel **physical**.

**Primary**

- premium proportions
- subtle multi-layer gradient
- thin border · soft inner highlight · restrained shadow

| State | Hành vi |
|-------|---------|
| Hover | cursor-following radial gradient (interpolated), slight lift, brighter border |
| Pressed | slight downward movement, darker gradient, compressed glow, smaller shadow |
| Disabled | muted, flat, quiet |
| Focus | elegant focus ring |

**No flashy effects.**

---

## Input design

- Editable fields **must** look editable; read-only **must** look read-only.
- Generated metadata **never** resembles editable inputs.
- Focus should feel premium (shell owns chrome — xem engineering).

---

## Motion

Motion **communicates state** — never decorate.

| Rule | Value |
|------|-------|
| Duration | 120–220 ms |
| Easing | OutCubic · OutQuart |
| Avoid | bounce · elastic |

Cursor-following effects: interpolate, stop immediately on hover leave, negligible CPU. Smoothing: `current += (target - current) * 0.15`.

---

## Consistency

Visual quality **và** consistency đều quan trọng.

- Không polish một màn hình làm app cảm giác lệch pha.
- Pattern mới xuất hiện 2 lần → đề xuất đưa vào design system ([`monostudio.md`](monostudio.md)).

---

## Self-review (design critique)

Trước khi coi task xong:

- hierarchy · optical balance · spacing rhythm · typography
- interaction · motion · consistency
- accessibility · performance · maintainability

Nếu dưới mức commercial quality → cải thiện trước khi ship.

---

## Final check

1. Có cải thiện MonoStudio không?
2. Có **premium** hơn (không flashy) không?
3. Có dễ maintain / systemize không?
4. Có giảm cognitive load không?
5. Có “ở nhà” bên cạnh Sonoma / Linear / Raycast / Arc / Figma / Resolve không?

**Bất kỳ câu nào “No” → reconsider.**

---

## Mission

Không optimize cho visual novelty. Optimize cho **daily professional use**.

Artist tập trung vào công việc, không phải interface. Interface tốt nhất là interface user **ngừng để ý**.

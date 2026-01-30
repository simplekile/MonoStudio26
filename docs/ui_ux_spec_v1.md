MONOSTUDIO 26 — UI/UX SPEC v1

(Frontend concept – framework-agnostic, Qt-first)

0. TRIẾT LÝ THIẾT KẾ (BẮT BUỘC TUÂN THEO)

Browse-first, Action-second

UI để duyệt & hiểu, không phải để bấm nút

Inspector = Info, không phải Form

Action luôn nằm trong context

Chuột phải / toolbar theo ngữ cảnh

Filesystem là sự thật

UI không “tạo thực tại riêng”

Không modal nếu không cần

Ưu tiên inline / popup nhỏ

1. CẤU TRÚC MÀN HÌNH TỔNG THỂ

┌──────────────┬───────────────────────────────┬──────────────┐
│ Sidebar      │ Main View                     │ Inspector    │
│              │                               │              │
│ Assets       │  Grid / List                  │ Name         │
│ Shots        │  Search / Filter              │ Type         │
│              │                               │ Path         │
│              │                               │ Departments  │
│              │                               │ Versions     │
└──────────────┴───────────────────────────────┴──────────────┘

Tỷ lệ

Sidebar ~15%

Main View ~60%

Inspector ~25%

Inspector panel uses dynamic visibility.It is hidden by default and only appears when a valid item is selected.

2. SIDEBAR SPEC

Chức năng

Chuyển context cấp cao

Assets

Shots

UI

Vertical list

Icon + label

Single selection

Không tree

Không sub-level

Hành vi

Click → reload Main View

Không ảnh hưởng Inspector nếu chưa select item

Không được

Không show asset list ở đây

Không filter

Không action

3. MAIN VIEW SPEC (TRÁI TIM CỦA APP)

Mode

Tile (default)

List

User được switch, app nhớ trạng thái.

3.1 Tile View

Mỗi tile gồm:

┌──────────────┐
│  Thumbnail   │  ← placeholder icon theo type
│              │
├──────────────┤
│ Name         │
│ Type badge   │
└──────────────┘

Interaction

Single click → select

Double click → enter (asset → departments)

Right click → context menu

3.2 List View

Columns

Name

Type

Departments count

Path (ẩn mặc định)

Không inline edit.

3.3 Search & Filter

Search

Text input

Match name, path

Real-time

Không fuzzy ở v1

Filter

Type (char / prop / env / shot)

Department (anim / model / comp …)

4. HIERARCHY LOGIC (RẤT QUAN TRỌNG)

Assets

Assets
 └─ char_aya
     └─ model
     └─ rig
     └─ anim

Shots

Shots
 └─ sh010
     └─ anim
     └─ lighting
     └─ comp

Department là child, không phải tab.

5. INSPECTOR SPEC (CHỈ INFO)

Inspector luôn hiển thị item đang select.

5.1 Asset / Shot

Name

Type

Absolute Path

Created Date

Last Modified

5.2 Department

Department Name

Work Path

Publish Path

Latest Version

Version Count

Quy tắc

Không edit text

Không rename

Không publish button lớn

6. CONTEXT MENU SPEC

Asset / Shot

Open Folder

Create Department

Refresh

Department

Open Work Folder

Open Publish Folder

Publish…

View Versions

Version

Open File

Reveal in Explorer

Set as Current

7. PUBLISH UX (v1)

Trigger

Right click Department → Publish…

Publish Dialog

Source file (auto-detect)

Version preview (v003)

Comment (optional)

Behavior

Copy work → publish

Auto increment version

UI refresh ngay

8. EMPTY / ERROR STATES

Empty project → “Select a project root to begin”

Empty assets / shots → placeholder, không popup

Missing folder → warning icon, không auto-fix

9. VISUAL STYLE GUIDELINE

Dark theme

Neutral gray base

Một accent color

Không gradient

Không animation thừa

Spacing rộng

10. NHỮNG THỨ BỊ CẤM Ở v1

User / permission

Task / assignee

Notes / comments

Status workflow

Shot dependency

Cloud / server

AI

11. TIÊU CHÍ THÀNH CÔNG

Artist mở app biết dùng ngay

Duyệt nhanh hơn Explorer

Publish không gây sợ


❌ QComboBox — Absolute NO Rules (MonoStudio 26)

Tuyệt đối KHÔNG dùng QComboBox khi:

Giá trị không được lưu như một state thực

Không ghi vào filesystem

Không ghi vào config rõ ràng

Không có Apply / Confirm

Inspector là read-only

Chỉ hiển thị thông tin

Không thay đổi dữ liệu

Không tạo side effect

Hành động là one-shot

Mở

Copy

Switch

Filter tạm thời
→ dùng Menu / Context Menu

Thay đổi không có hậu quả lâu dài

Đổi rồi refresh là mất

Không ảnh hưởng project thực
→ QComboBox gây hiểu nhầm

Control tồn tại chỉ để “cho tiện”

Không có spec state rõ ràng

Không có ownership rõ ràng
→ Không được dùng

Không thể giải thích bằng 1 câu ngắn

“Cái này là gì?”

“Nó có lưu không?”
→ FAIL UX → cấm dùng

Patch-level (v1.x)

Pain-point patch

Polish

UX clarity
→ Không introduce ComboBox mới

✅ Thay thế đúng

Menu / Context Menu → hành động explicit

Label + Tooltip → thông tin

Button → trigger rõ ràng

Read-only field → dữ liệu thật

🧠 Design Principle Summary (1 dòng)

If it doesn’t represent a persistent, explicit, user-owned state — do not use QComboBox.


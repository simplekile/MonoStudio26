Menu Guideline → MonoStudio 26 UI Mapping

1. Project Switcher
Hiện tại (đúng hướng)

Project được chọn từ danh sách

Load project từ filesystem

Không có “apply”

Mapping guideline

✅ PHẢI là Menu

Global menu hoặc dropdown-style menu

Click = hành động load project

Không giữ “selected state” UI quá mạnh

Tuyệt đối tránh

❌ QComboBox
❌ Hiển thị như setting
❌ Gợi ý “current value” kéo dài

Điều chỉnh v1.1 (nếu cần)

Hover nhẹ (đã nói)

Không highlight project “đang active” quá nặng → vì filesystem mới là truth

2. Asset / Shot View Switch
Bản chất

Chỉ là view context

Không phải project state

Mapping guideline

✅ Menu hoặc Sidebar Section

Assets

Shots

❌ Không combo box
❌ Không dropdown có label “Type”

Rule chốt

Nếu user reload app mà view quay về default → đó không phải setting → menu

3. Search / Filter Bar
Hiện tại

Có search input

Users kỳ vọng “tìm thông minh”

Mapping guideline

✅ Filter = hành động tạm thời

Label: Filter

Hành vi: substring match

Optional (OK):

Menu nhỏ cạnh filter:

Clear Filter

Filter by Name (nếu muốn explicit)

❌ Không type selector combo
❌ Không multi-condition UI
❌ Không saved filter

4. Department List / Department Count
Bản chất

Reflect folder structure

Read-only info

Mapping guideline

✅ Không menu cho việc “chọn department” (nếu chỉ là hiển thị)
✅ Tooltip giải thích count

Context Menu (OK):

Copy Path

Reveal Folder (nếu đã có)

❌ Không “Select Department” combo
❌ Không filter department bằng dropdown

5. Inspector Panel
Bản chất

Read-only

Information only

Mapping guideline

✅ Context Menu ONLY

Copy Full Path

Copy Name

❌ Không combo
❌ Không dropdown
❌ Không editable field trá hình

Inspector không bao giờ chứa menu tạo state.

6. Thumbnail / Item Tile Area
Bản chất

Representation

Không điều khiển logic

Mapping guideline

✅ Context Menu cho item-level actions

Open Folder

Copy Path

Reveal Info

❌ Không view-mode combo box trong tile
❌ Không icon dropdown nhỏ trong mỗi item

Menu phải rõ là hành động, không phải decoration.

7. Sidebar (Assets / Shots / etc.)
Bản chất

Navigation

Section switching

Mapping guideline

✅ Sidebar click = menu hành động

Không cần combo

Không cần dropdown

Nếu có submenu:

Tối đa 1 cấp

Không cascade sâu

8. “Type”, “Mode”, “View” (những thứ hay bị combo hóa)
Rule áp thẳng
Thứ	Cách đúng
Type	Menu
Mode	Menu
View	Menu
Switch	Menu
Select	Menu

Nếu UI control chỉ tồn tại để “đổi cái đang xem” → menu

9. Những thứ TUYỆT ĐỐI KHÔNG ĐƯỢC XUẤT HIỆN trong UI hiện tại

❌ QComboBox cho:

Project

Asset type

Shot type

Filter

Department

View mode

❌ Dropdown giả dạng:

ComboBox nhưng disable edit

ComboBox chỉ để click

❌ UI gợi ý state:

“Current Filter”

“Active Type”
(trừ khi thật sự có persistent state – hiện tại không có)

10. Quick Audit Checklist (dùng mỗi lần review UI)

Trước mỗi control mới:

Đây là hành động hay setting?

Reload app có mất không?

Có ghi filesystem không?

Có cần Apply / Save không?

➡️ Nếu KHÔNG / CÓ MẤT → MENU


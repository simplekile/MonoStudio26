Menu Design Guideline

MonoStudio 26

1. Mục tiêu

Menu trong MonoStudio 26 tồn tại để:

Kích hoạt hành động rõ ràng

Tránh tạo state ngầm

Giữ UI trung thực với filesystem

Thể hiện triết lý: explicit > convenient

Menu không phải là setting system.

2. Khi nào PHẢI dùng Menu

Dùng Menu / Context Menu khi hành động:

One-shot

Switch project

Filter view

Change view mode

Copy / reveal info

Không tạo state lâu dài

Không ghi filesystem

Không ghi config

Refresh là mất

Ảnh hưởng UI, không ảnh hưởng data

View

Visibility

Ordering

Filtering

Inspector hoặc read-only context

Không chỉnh sửa

Không side-effect

Điều hướng

Chuyển project

Chuyển asset / shot

Chuyển section

Nếu người dùng không thể trả lời câu
“Lựa chọn này có được lưu không?”
→ phải dùng Menu

3. Khi nào TUYỆT ĐỐI KHÔNG dùng Menu

Menu không được dùng khi:

Cần nhập dữ liệu

Cần chỉnh sửa giá trị

Cần xác nhận nhiều bước

Có state phức tạp, phụ thuộc lẫn nhau

👉 Khi đó dùng Dialog (explicit confirm).

4. Menu vs QComboBox (Rule cứng)
Trường hợp	Dùng Menu	Dùng QComboBox
Filter	✅	❌
Project switch	✅	❌
View mode	✅	❌
Inspector	✅	❌
One-shot action	✅	❌
Persistent setting	❌	⚠️ (rất hiếm)

QComboBox chỉ được phép cho persistent, user-owned settings
có Apply / Save semantics rõ ràng.

5. Menu Structure Rules
5.1 Không quá sâu

Tối đa 2 cấp

Tránh submenu lồng submenu

5.2 Nhóm theo hành vi

Điều hướng

Hiển thị

Thông tin

Hành động

Không nhóm theo “logic code”.

6. Label & Copy Rules
6.1 Action-first

Bắt đầu bằng động từ

Switch Project

Filter by Name

Copy Full Path

❌ Tránh:

Abstract words

Technical terms

Internal naming

6.2 Không mập mờ state

❌ Không dùng:

Current

Active

Selected (trừ khi thật sự có state)

7. Menu Visual Rules

Không animation

Không hover quá mạnh

Không icon nếu không cần thiết

Ưu tiên text clarity

Menu phải:

Nhẹ

Nhanh

Không gây chú ý quá mức

8. Context Menu Rules

Context Menu dùng khi:

Hành động liên quan trực tiếp đến item

Không có global meaning

Ví dụ:

Copy Path

Reveal Info

Open Folder (nếu có)

Không dùng context menu cho:

Global filter

Project-wide behavior

9. Failure Test (Bắt buộc)

Trước khi merge, hỏi 3 câu:

Menu này có tạo state ngầm không?

Nếu refresh app, menu choice có mất không?

User có hiểu menu này là hành động, không phải setting không?

Nếu bất kỳ câu nào mơ hồ → thiết kế sai.

10. One-line Principle (dán lên đầu file)

In MonoStudio 26, menus represent explicit actions, never hidden state.


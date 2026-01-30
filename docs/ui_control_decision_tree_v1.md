UI Control Decision Tree

MonoStudio 26

Start here every time you need a new UI control

STEP 1 — User intent là gì?
A. Chỉ xem thông tin

→ Label / Read-only Field

Không chỉnh

Không trigger logic

Không side-effect

❌ Không combo
❌ Không menu tạo state

B. Kích hoạt hành động (one-shot)

→ Menu / Button

Ví dụ:

Switch project

Filter view

Copy path

Reveal folder

➡️ Đi tiếp STEP 2

C. Thay đổi dữ liệu hoặc setting

→ Dialog / Form (Apply / Confirm)

Có ownership

Có commit point

Có revert khả năng

❌ Không làm trực tiếp trên main UI

STEP 2 — Hành động có tạo state lâu dài không?
❌ Không tạo state

→ Menu

Mất khi refresh

Không ghi filesystem

Không ghi config

Ví dụ:

Filter

View mode

Navigation

✅ Có tạo state

→ Đi STEP 3

STEP 3 — State đó có được lưu explicit không?
❌ Không lưu rõ ràng

→ KHÔNG được phép tồn tại

Refactor lại thành Menu

Hoặc xóa feature

✅ Có lưu

→ Đi STEP 4

STEP 4 — State thuộc về ai?
A. Filesystem / Project

→ Không chỉnh trong UI

Chỉ phản ánh

Read-only

Inspector

B. User-owned setting

→ Đi STEP 5

STEP 5 — Có Apply / Save semantics không?
❌ Không

→ KHÔNG dùng QComboBox

Dùng Dialog

Dùng explicit action

✅ Có

→ QComboBox (RẤT HIẾM)

Chỉ cho:

Theme

Language

Global preference

FINAL RULES (cứng)

Menu > ComboBox trong 90% trường hợp

QComboBox = persistent setting only

Inspector = zero interactive state

Không giải thích được bằng 1 câu → thiết kế sai

ONE-LINE SUMMARY (dán đầu doc)

If a choice does not represent a persistent, explicit, user-owned state — it must not be a QComboBox.

Sanity Check (trước khi merge)

Hỏi:

Reload app, control này còn tác dụng không?

Có cần Apply không?

User có nghĩ đây là setting không?

Nếu bất kỳ câu trả lời nào mơ hồ → STOP


Department Template Spec

MonoStudio 26

1. Mục đích

Department Template định nghĩa tập tên department hợp lệ
được đề xuất khi tạo Asset / Shot.

Template không tạo folder tự động
và không enforce cấu trúc.

2. Phạm vi áp dụng

Chỉ dùng tại Create Asset và Create Shot

Không dùng cho:

Existing assets/shots

Auto-sync

Validation

Repair

3. Nguồn dữ liệu

Template được đọc từ project-level config:

project_root/
  .monostudio/
    department_templates.json


UI chỉ đọc

Không chỉnh sửa từ UI

Không cache ngoài bộ nhớ runtime

4. Cấu trúc dữ liệu (JSON)
{
  "asset_types": {
    "Character": ["model", "rig", "anim", "fx", "lighting"],
    "Prop": ["model"],
    "Environment": ["layout", "model", "lighting"]
  },
  "shot_types": {
    "Shot": ["layout", "anim", "fx", "lighting"]
  }
}

5. Ngữ nghĩa quan trọng

Key (ví dụ: Character, Prop, Shot)

Phải khớp type selector trong Create Asset / Shot

Value

Là vocabulary hợp lệ

Không mang ý nghĩa workflow

Không imply bắt buộc

6. Hành vi UI (bắt buộc)

Khi user chọn type:

UI load danh sách department tương ứng

UI render dưới dạng checkbox list

TẤT CẢ checkbox = OFF mặc định

Template:

Đề xuất tên

Không quyết định hành động

7. Commit vào filesystem

Folder chỉ được tạo khi:

User tick checkbox

Và bấm Create Asset / Shot

Không tick → không tạo

Không auto-create

Không hậu xử lý

8. Sub-department (work / publish)

Là tuỳ chọn UI

Default = OFF

Nếu ON:

Tool tạo work/ và publish/ bên trong department

Template không định nghĩa sub-department

9. Không được phép

❌ Auto-select checkbox
❌ Tạo folder ngầm
❌ Ghi nhớ lựa chọn trước
❌ Validate naming ngoài template
❌ Sửa template từ UI

10. Nguyên tắc chốt (1 dòng)

Templates define valid names.
Checkboxes express intent.
Creation commits to filesystem.


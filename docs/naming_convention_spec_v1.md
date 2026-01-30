MONOSTUDIO 26 — NAMING CONVENTION SPEC v1

0. MỤC TIÊU

Dễ đọc bằng mắt

Dễ dùng trong pipeline & DCC

Ổn định lâu dài

Không encode logic UI / workflow

Không cần migrate sớm

Filesystem phục vụ sản xuất, không phục vụ UI.

1. NGUYÊN TẮC CHUNG (BẮT BUỘC)

Lowercase + underscore (_)

Không space

Không ký tự đặc biệt

Không encode version / trạng thái

Tên folder = danh tính, không phải hành vi

2. PROJECT
Project Root

Tên folder project tự do, readable

Không encode ngày / version

Ví dụ:

Project_ForestSpirit
short_film_vein

System Folder
.monostudio/


System namespace

Không phải dữ liệu sản xuất

Không chỉnh tay

3. ASSETS
3.1 Asset Type (level 1)

Dùng category rõ nghĩa

Không dùng số thứ tự

Ví dụ:

assets/
 ├─ char/
 ├─ prop/
 ├─ env/
 ├─ fx/


❌ Không dùng:

01_char
characters_v2

3.2 Asset Name (level 2)

Format khuyến nghị:

<type>_<name>


Ví dụ:

char_aya
char_oldman
prop_sword
env_forest


Không bắt buộc enforce, nhưng rất nên dùng.

❌ Không dùng:

aya
sword_final
forest_new

4. SHOTS
4.1 Shot Structure (v1 — KHÔNG SEQUENCE)

Shots ở v1 là flat list, không có sequence.

shots/
 ├─ sh010/
 ├─ sh020/
 └─ sh030/

4.2 Shot Name

Format khuyến nghị:

sh###  (zero-padded)


Ví dụ:

sh010
sh020
sh105


Lý do:

Dễ sort

Dễ đọc

Chuẩn ngành

❌ Không dùng:

shot_1
scene3_shot2
01_sh010

5. DEPARTMENTS

Department là child folder, không phải tab.

Ví dụ:

char_aya/
 ├─ model/
 ├─ rig/
 └─ anim/

Quy tắc:

Lowercase

1 từ nếu có thể

Không encode version

Ví dụ hợp lệ:

model
rig
anim
fx
lookdev
lighting
comp

Custom names are allowed (team convention), ví dụ:

cloth
groom

6. WORK / PUBLISH
Chuẩn thư mục:
<department>/
 ├─ work/
 └─ publish/


work/: file đang làm

publish/: output chính thức

❌ Không đổi tên
❌ Không thêm suffix

7. VERSION (FOLDER-BASED)
Publish version folder:
v001
v002
v010


Zero-padded

Không chữ thêm

❌ Không dùng:

version1
final
latest
v1_final

8. NHỮNG THỨ TUYỆT ĐỐI KHÔNG LÀM

❌ Encode UI logic:

01_assets
02_shots


❌ Encode workflow:

approved
final
temp


❌ Encode user / task:

aya_anim_done
john_fix

9. ENFORCEMENT POLICY

v1: Không enforce bằng code

MonoStudio:

Không rename

Không validate

Không auto-fix

👉 Naming là team convention, không phải tool constraint.

10. FUTURE NOTES (KHÔNG ÁP DỤNG v1)

Sequence (seq01/) → v2+

Status system → v2+

Auto-validation → v2+

11. TÓM TẮT 6 DÒNG (DÁN LÊN TƯỜNG)

Không seq ở v1

Shots flat

Prefix semantic tốt

Prefix số là sai

Tên folder = danh tính

Filesystem không phục vụ UI


# MonoStudio — Yêu cầu tích hợp cho tool bên ngoài (MonoFXSuite)

Tài liệu này dành cho team phát triển **MonoFXSuite** (hoặc tool tương tự) để MonoStudio có thể:
- Hiển thị **phiên bản đang cài** của tool trong Settings → Updates.
- Cho phép user **tải và cài bản mới** trực tiếp từ MonoStudio (nút Download).

---

## 1. Vị trí cài đặt (để MonoStudio đọc được version)

MonoStudio cài mặc định tại: **`C:\Program Files\MonoStudio26`** (hoặc thư mục user chọn khi cài).

**MonoFXSuite nên cài vào một trong hai vị trí sau:**

### Option A — Cùng gốc với MonoStudio (khuyến nghị)

- **Đường dẫn**: `{Thư mục cài MonoStudio}\tools\MonoFXSuite\`  
  Ví dụ: `C:\Program Files\MonoStudio26\tools\MonoFXSuite\`
- Khi build installer (Inno Setup / MSI), mặc định **DefaultDirName** nên trỏ tới:
  - `{pf}\MonoStudio26\tools\MonoFXSuite`  
  hoặc cho user chọn thư mục cài MonoStudio rồi thêm `\tools\MonoFXSuite`.

### Option B — Thư mục user (không cần quyền Admin)

- **Đường dẫn**: `%LOCALAPPDATA%\MonoStudio\tools\MonoFXSuite\`  
  Ví dụ: `C:\Users\<User>\AppData\Local\MonoStudio\tools\MonoFXSuite\`

MonoStudio sẽ tìm file VERSION theo thứ tự: Option A trước, sau đó Option B.

---

## 2. File VERSION (bắt buộc để MonoStudio hiển thị “phiên bản hiện tại”)

Sau khi cài, trong thư mục gốc của MonoFXSuite **phải có file VERSION** tại một trong hai vị trí:

- **Cách 1**: `tools\MonoFXSuite\VERSION`
- **Cách 2**: `tools\MonoFXSuite\monofxsuite_data\VERSION` (nếu project có cấu trúc thư mục kiểu `monofxsuite_data` giống MonoStudio)

**Nội dung file VERSION**: một dòng duy nhất, format phiên bản semantic.

- Ví dụ: `1.0.2` hoặc `v1.0.2`
- MonoStudio sẽ hiển thị đúng chuỗi này (có hoặc không có tiền tố `v`) trong cột Version tại Settings → Updates.

**Khi build installer**: đảm bảo file VERSION được đóng gói và cài đúng vào một trong hai đường dẫn trên (tương ứng với Option A hoặc B).

---

## 3. GitHub Release (để MonoStudio có nút “Download” và release notes)

- **Repo GitHub**: MonoStudio đã cấu hình repo của MonoFXSuite (ví dụ `simplekile/MonoFXSuite`).
- Mỗi bản phát hành cần:
  1. **Tag**: theo format version, ví dụ `v1.0.2` (khớp với nội dung file VERSION).
  2. **Release**: tạo Release từ tag đó trên GitHub.
  3. **Asset**: đính kèm **file cài đặt Windows** (ví dụ `MonoFXSuite_Setup.exe`) vào Release.
  4. **Release notes**: nội dung phần “body” của Release sẽ được MonoStudio hiển thị trong mục Release notes khi user bấm “Check for updates”.

MonoStudio sẽ:
- Gọi GitHub API để lấy release mới nhất.
- Chọn asset có đuôi `.exe` hoặc tên chứa “setup” làm file tải.
- Hiển thị nút **Download** trong hàng MonoFXSuite; khi user bấm, tải file đó và chạy installer (MonoStudio không thoát app khi cài tool khác).

---

## 4. Tóm tắt checklist cho team MonoFXSuite

- [ ] **Installer** cài MonoFXSuite vào `{MonoStudio install dir}\tools\MonoFXSuite\` (hoặc `%LOCALAPPDATA%\MonoStudio\tools\MonoFXSuite\`).
- [ ] **File VERSION** có trong bản cài tại `VERSION` hoặc `monofxsuite_data\VERSION`, nội dung một dòng (vd. `1.0.2`).
- [ ] **GitHub Release**: tag dạng `v1.0.2`, có đính kèm file `.exe` installer; điền release notes (body) cho mỗi release.

Sau khi thỏa mãn các mục trên, MonoStudio sẽ tự nhận phiên bản đang cài và hiển thị nút Download khi có bản mới.

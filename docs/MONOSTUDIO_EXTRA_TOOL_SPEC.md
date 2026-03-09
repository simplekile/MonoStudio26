# MonoStudio — Yêu cầu tích hợp cho tool bên ngoài (MonoFXSuite)

Tài liệu này dành cho team phát triển **MonoFXSuite** (hoặc tool tương tự) để MonoStudio có thể:
- Hiển thị **phiên bản đang cài** của tool trong Settings → Updates.
- Cho phép user **tải và cài bản mới** trực tiếp từ MonoStudio (nút Download).

---

## 1. Vị trí cài đặt (để MonoStudio đọc được version)

MonoStudio **không** luôn cài ở `C:\Program Files\` — user có thể chọn thư mục khác khi cài. Để installer MonoFXSuite điền đúng đường dẫn “Under MonoStudio”:

**MonoStudio ghi đường dẫn cài thực tế** vào file:
- **`%LOCALAPPDATA%\MonoStudio\install_path.txt`**  
  Nội dung: một dòng là đường dẫn thư mục cài MonoStudio (vd. `D:\Apps\MonoStudio26`). File được cập nhật mỗi lần chạy MonoStudio.

**Installer MonoFXSuite (Option A — Under MonoStudio) nên:**
1. Đọc file `%LOCALAPPDATA%\MonoStudio\install_path.txt`.
2. Nếu tồn tại và hợp lệ: mặc định đường dẫn = `{path trong file}\tools\MonoFXSuite`.
3. Nếu không có file (MonoStudio chưa từng chạy): fallback `C:\Program Files\MonoStudio26\tools\MonoFXSuite` hoặc để user duyệt chọn thư mục MonoStudio.

**Trang “Install location” trong installer MonoFXSuite (đã triển khai):**

| Lựa chọn | Đường dẫn mặc định | MonoStudio nhận version? |
|----------|---------------------|---------------------------|
| **Under MonoStudio** (mặc định) | Đọc từ `install_path.txt` + `\tools\MonoFXSuite`; fallback `{pf}\MonoStudio26\tools\MonoFXSuite` | Có |
| **User folder** | `%LOCALAPPDATA%\MonoStudio\tools\MonoFXSuite` | Có |
| **Standalone** | Trang sau: chọn thư mục (mặc định `{autopf}\MonoFXSuite`) | Không — cột Version hiển thị "—", vẫn có nút Download. |

Trên trang “Select Destination Location”, đường dẫn điền sẵn theo lựa chọn; user có thể sửa. MonoStudio tìm VERSION: Option A trước, rồi Option B. Option C không nằm trong hai path đó.

---

## 2. File VERSION (bắt buộc để MonoStudio hiển thị “phiên bản hiện tại”)

Sau khi cài, trong **thư mục gốc** của MonoFXSuite (`{app}`) **phải có file VERSION**.

- **Vị trí**: `tools\MonoFXSuite\VERSION` (tức `{app}\VERSION` khi cài Option A/B).
- MonoFXSuite installer (vd. trong `.iss`): `Source: "..\..\VERSION"; DestDir: "{app}"; Flags: ignoreversion` — bản cài luôn có VERSION ở thư mục gốc.

**Nội dung file VERSION**: một dòng duy nhất, format semantic (vd. `1.0.2` hoặc `v1.0.2`). MonoStudio hiển thị đúng chuỗi này trong cột Version tại Settings → Updates.

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

## 4. Tóm tắt checklist (đã triển khai bên MonoFXSuite)

- [x] **VERSION** trong bản cài ở thư mục gốc `{app}` (MonoFXSuite.iss: `Source: "..\..\VERSION"; DestDir: "{app}"`).
- [x] **Trang “Install location”**: Option A (Under MonoStudio, mặc định), Option B (User folder), Option C (Standalone); đường dẫn điền sẵn trên trang Select Destination, user có thể sửa.
- [x] **GitHub Release**: tag `vx.y.z`, release, RELEASE_NOTES.md, đính kèm `.exe` — đúng spec.

Build installer: `build/output/MonoFXSuite_Setup.exe`. Khi user chọn Option A hoặc B, MonoStudio đọc được phiên bản đang cài và hiển thị nút Download khi có bản mới.

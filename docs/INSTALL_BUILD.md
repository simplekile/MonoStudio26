# Build & Installer — MonoStudio 26

Cách đóng gói app thành thư mục chạy được (onedir) và tạo file cài đặt Windows (.exe).

## Chuẩn bị

- Python 3.10+ (đã dùng cho dev)
- Cài dependency build:  
  `pip install PySide6 pyinstaller`
- (Tùy chọn) [Inno Setup 6](https://jrsoftware.org/isinfo.php) — để tạo installer `.exe` một file

## Bước 1: Build PyInstaller (onedir)

Từ thư mục gốc repo:

```powershell
pyinstaller monostudio26.spec
```

Kết quả: thư mục `dist/MonoStudio26/` chứa `MonoStudio26.exe` và toàn bộ thư viện + data (icons, pipeline, fonts). Chạy trực tiếp:

```powershell
dist\MonoStudio26\MonoStudio26.exe
```

## Bước 2: Tạo installer Windows (Inno Setup)

1. Cài [Inno Setup 6](https://jrsoftware.org/isinfo.php).
2. Đảm bảo đã chạy bước 1 (có `dist/MonoStudio26/`).
3. Mở file `installer/MonoStudio26.iss` trong Inno Setup Compiler (hoặc chạy):

   ```powershell
   iscc installer\MonoStudio26.iss
   ```

4. File cài đặt tạo ra: `dist/MonoStudio26_Setup.exe`. Phát cho user, chạy để cài vào thư mục (mặc định `%LOCALAPPDATA%\MonoStudio26`) và tạo shortcut Desktop/Start Menu.

## Script một lệnh

```powershell
.\build_installer.ps1
```

Script sẽ: (1) chạy PyInstaller theo `monostudio26.spec`, (2) nếu tìm thấy `iscc` trong PATH thì tự build luôn installer.

## Ghi chú kỹ thuật

- **Base path**: App dùng `monostudio.core.app_paths.get_app_base_path()` — khi chạy từ PyInstaller trả về `sys._MEIPASS` (thư mục chứa exe trong onedir), khi chạy từ source trả về repo root.
- **Config user**: Hiện tại app lưu settings vào `monostudio_data/config/` (trong thư mục cài đặt). Nếu sau này muốn tách config theo user (vd. `%APPDATA%\MonoStudio26`), cần sửa `_app_settings_path()` trong `main_window.py` để khi frozen trỏ vào thư mục user.

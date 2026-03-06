# MonoStudio 26 — Build & Release (quick reference)

## Quy trình một lệnh

1. **Build** (từ repo root, PowerShell):
   ```powershell
   .\build_installer.ps1
   ```
   - Tự commit nếu có thay đổi chưa commit (bỏ qua: `-NoCommit`).
   - Thứ tự: app.ico → VERSION → PyInstaller onedir → Inno Setup (nếu có `iscc`).
   - Output: `dist/MonoStudio26/`, `dist/MonoStudio26_Setup.exe`.

2. **Push** (tùy chọn):
   ```powershell
   git push
   ```

3. **Publish release** (sau khi build xong):
   ```powershell
   .\publish_release.ps1
   ```
   - Cần: GitHub CLI (`gh`), đã `gh auth login`.
   - Đọc tag từ `monostudio_data/VERSION`, đính kèm `dist/MonoStudio26_Setup.exe`, tạo release.
   - Release notes: ưu tiên `RELEASE_NOTES.md` (hoặc `CHANGELOG.md`); không có thì sinh từ git log.

## Lưu ý

- **Commit xong mới build** — version lấy từ `git rev-list --count HEAD`.
- Chi tiết đầy đủ: `.cursor/rules/rule_build_v1.mdc`.

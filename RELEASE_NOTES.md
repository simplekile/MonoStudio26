# MonoStudio 26 — Release v26.11.2

## Highlights

- **Windows subprocess — ẩn cửa sổ console**: Module `subprocess_win` với `hide_console_subprocess_kwargs` để spawn process (decode preview, thumbnail) không bật cửa sổ console trên Windows.
- **Preview & thumbnail**: Tiếp tục chỉnh `sequence_preview_decode` và `thumbnails` đồng bộ luồng decode/spawn.

## Changes in this release

- feat: `subprocess_win.py` — kwargs chuẩn cho subprocess ẩn console trên Windows.
- fix: `sequence_preview_decode.py` — dùng helper subprocess Windows + tinh chỉnh decode.
- fix: `thumbnails.py` — spawn subprocess nhất quán với preview.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.11.1

## Highlights

- **Sequence preview — patch**: Ổn định hóa core preview chuỗi, decode UI và thumbnail để xem media/sequence nhất quán hơn, ít edge case khi đổi selection hoặc nguồn file.

## Changes in this release

- fix: `sequence_preview.py` — điều chỉnh logic preview chuỗi.
- fix: `sequence_preview_decode.py` — decode / hiển thị khớp pipeline main view.
- fix: `thumbnails.py` — đồng bộ thumbnail với preview đã cập nhật.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.11.0

## Highlights

- **Production status — preset & pipeline**: Hệ thống trạng thái sản xuất theo preset JSON (`production_status_presets.json`), merge theo project, registry có category/rank/tooltip; UI chọn status qua menu thống nhất; đồng bộ grid, Inspector và aggregate status.
- **Sequence / media preview**: Cải tiến luồng xem preview chuỗi (core + decode UI), gắn với Inspector và main view để review nhanh hơn.
- **DCC — Fusion**: Thêm Fusion vào registry pipeline, adapter launch, và brand icon (hỗ trợ PNG khi không có SVG).
- **Build & phân phối**: Cờ `-Beta` cho installer tên `MonoStudio26_Beta_Setup.exe` và tên hiển thị “MonoStudio 26 (Beta)”; `-NoVersionBump` để build đúng VERSION đã ghi tay (patch/hotfix không bị `build_version.py` tự tăng).
- **Nền tảng đã có từ các bản 26 trước**: Banner update bền, List mode Assets/Shots, sidebar compact, watcher toggle + rename asset an toàn hơn trên Windows — vẫn là phần cốt lõi của trải nghiệm trong cùng major 26.

## Changes in this release

- feat: **Production status** — module `production_status` (registry, category màu, thứ tự menu), preset mặc định + override theo project; tích hợp `item_status` / `models` / `fs_reader`.
- feat: **UI production status** — `production_status_menu`, cập nhật Inspector, main view, main window, style; stress runner nếu có điểm chạm status.
- feat: **Sequence preview** — chỉnh `sequence_preview`, `sequence_preview_decode`, UI liên quan (inspector, thumbnails, top bar, style).
- feat: **Fusion DCC** — `dcc_fusion`, wiring trong `app_controller`, `dccs.json`, icon thương hiệu.
- feat: **Brand icons** — fallback PNG + cache theo mtime file để đổi icon không cần restart.
- build: **Inno** — `MyAppName` / `MyOutputBaseFilename` có thể override (beta); **PowerShell** — `-Beta`, `-NoVersionBump` trên `build_installer.ps1`.

## Lưu ý migration (pipeline)

- Nếu project dùng production status, cần có (hoặc merge) `production_status_presets.json` trong pipeline; status id cũ có thể cần map sang preset mới — kiểm tra override từng item sau khi mở project.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

---

# MonoStudio 26 — Release v26.10.3

## Highlights

- **Update notifications – persistent banner**: Khi có bản MonoStudio mới, app hiển thị banner callout cố định dưới nút Update ở TopBar (nội dung `Update available: vX.Y.Z. Check it out!`), chỉ ẩn khi user chủ động đóng.
- **Notification system – important banner API**: Thêm `notify.important(...)` để hiển thị banner quan trọng (update, walkthrough lần đầu…) bám vào bất kỳ anchor widget nào, dùng lại được cho các flow hướng dẫn sau này.
- **Assets/Shots — List mode hoàn chỉnh**: List view mới với thumbnail, DCC badges, status màu, Version và Last Updated rõ ràng, đồng bộ với tile mode.
- **Sidebar — Compact + responsive layout**: Sidebar icon‑only 56px cho màn hình hẹp, giữ đầy đủ scope (Projects/Shots/Assets) và nav (Inbox/Guide/Outbox), project switcher chuyển sang Sidebar, Recent Tasks/Filters hỗ trợ popup trong compact.
- **File watcher toggle (Top bar)**: Nút mắt mới cạnh Notifications để bật/tắt toàn bộ filesystem watcher; khi tắt, MonoStudio giải phóng handle để rename/delete thư mục an toàn hơn, đặc biệt trên Windows + Dropbox.
- **Rename Asset ổn định hơn**: Luồng rename asset kiểm tra watcher đã tắt, chuẩn bị danh sách work file rename trước và dùng cơ chế retry/two‑step rename ở tầng core để giảm lỗi `Access is denied`.

## Changes in this release

- feat: Notification service — thêm loại toast `important` được lưu vào history nhưng hiển thị bằng `ImportantNotificationBanner` (QFrame non‑modal, anchor theo nút Update ở TopBar, có arrow callout và nút X).
- feat: Update check at startup — khi detect có bản mới, hiển thị banner callout `Update available: {latest_version}. Check it out!` thay vì tooltip tạm 5s.
- feat: Assets/Shots List mode — sắp xếp lại cột `#`, thumbnail, Name, DCC, Status, Version, Last Updated; Name bold, DCC dạng badge, Status tô màu theo trạng thái; Version/Last Updated đọc trực tiếp từ dữ liệu/version và mtime file.
- feat: List mode thumbnails — dùng chung pipeline với tile view, crop‑fit theo chiều cao dòng, prefetch thumbnail theo vùng đang scroll, sync invalidation giữa tile và list.
- feat: Sidebar compact & layout — thêm `SidebarCompact` icon‑only, project switcher/nút scope Projects/Shots/Assets/footer nav Inbox/Project Guide/Outbox/Filter/Recent Tasks; auto switch full ↔ compact theo kích thước window.
- feat: Filters & Recent Tasks popup — filter center có thể tách ra popup trong compact sidebar; Recent Tasks ở compact mở bằng popup riêng cạnh đáy, dùng chung popup toggle pattern (grace period + clear hover state).
- feat: MainView card size slider — slider thumbnail 5 mức scale (0.2–1.0) hiển thị trong popup dưới nút; logic tính card width tách khỏi viewport nên không auto‑scale khi resize window.
- feat: Top bar watcher toggle — `eye` xanh lá khi đang theo dõi, `eye-off` đỏ khi đã tắt; thêm trạng thái busy trong lúc app đang hủy watcher và worker scan.
- feat: Asset rename flow — chỉ cho phép Rename/Delete khi watcher đã tắt; hiển thị cảnh báo hướng dẫn người dùng tắt watcher trước khi thao tác.
- feat: Core asset_rename — chuẩn hóa `prepare_work_file_renames()` để tính trước danh sách work file cần đổi tên, tránh mở lại thư mục asset ngay trước khi rename folder (giảm khả năng giữ handle trên Windows).
- ux: Khi bật/tắt watcher, hiển thị toast `File watcher paused. Rename and delete are now allowed.` / `File watcher on. Changes will be detected automatically.`.

## Install

Tải **MonoStudio26_Setup.exe** từ Assets bên dưới và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

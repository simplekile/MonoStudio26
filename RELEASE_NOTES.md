# MonoStudio 26 — Release v26.10.2

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

# MonoStudio 26 — Release v26.17.1

## Highlights

- **Fix explorer thumbnails**: Async decode dùng `Signal` + `QImage` thay `QMetaObject.invokeMethod` — hết crash `Unable to find a QMetaType for "object"` trên Inbox/Outbox/Guide.
- **v26.17.0** (chưa publish trước đó): DJV sidecar, OCIO sequence review, deep link reveal, async UI workers, video player settings.

## Changes in this release

- fix: `explorer_thumbnail_loader.py` — cross-thread thumbnail decode via queued signal.
- includes: DJV, OCIO, deep links, explorer sort, `ui_worker_loop` (from v26.17.0).

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg** / **DJV View**: Settings → General / Updates.

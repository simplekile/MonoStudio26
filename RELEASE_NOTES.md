# MonoStudio 26 — Release v26.17.2

## Highlights

- **Video export**: Progress theo từng range; FFmpeg `-progress pipe:1`; cancel giữa chừng; export nhiều đoạn ổn định hơn.
- **Sequence flipbook**: Decode bucket theo viewport — chấp nhận plate nhỏ hơn khi đủ sharp; không giữ frame bucket cũ khi resize.
- **Review player**: Preview dialog & playback backend đồng bộ export/decode.

## Changes in this release

- fix: `video_export_dialog.py`, `video_media.py` — ranged export progress + cancel.
- fix: `review_playback_backend.py`, `video_preview_dialog.py` — sequence decode display bucket.
- test: `test_sequence_decode_backend.py`.

## Install

Tải **MonoStudio26_Setup.exe** từ GitHub Releases và chạy. Installer sẽ đóng app nếu đang mở để cập nhật.

**libmpv** / **FFmpeg** / **DJV View**: Settings → General / Updates.

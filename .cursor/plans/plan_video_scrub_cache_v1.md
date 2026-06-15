# Plan: Video Scrub Cache (v1)

> Mirror of [plan_video_scrub_cache_v1.mdc](../rules/plan_video_scrub_cache_v1.mdc)

## Tóm tắt

Cache **chỉ** cho thumbnail hover/scrub popup (FFmpeg). Video embed (mpv) scrub giữ `prime_for_scrub` + `seek` — **không** couple với cache.

## Phase

1. **Memory LRU** — module `VideoScrubThumbCache`, hit trước khi spawn worker
2. **Prefetch keyframe** — nền sau mở video, cancel khi đóng/đổi file
3. **Disk optional** — `monostudio_data/cache/video_scrub/` (gitignored)

## Bài học quan trọng

Scrub mpv hỏng do các fix backend (scrub session, micro-unpause), **không** do cache hover. Khi implement lại cache, không đụng `MpvEmbeddedBackend.seek`.

Chi tiết đầy đủ: `.cursor/rules/plan_video_scrub_cache_v1.mdc`.

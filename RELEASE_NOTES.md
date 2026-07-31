# MonoStudio 26.18.0

## Dialog tier design system
- **Tier 1 / Tier 2** dialog shells (`dialog_tier/`) — frozen golden reference cho New Project, Create Asset/Shot flows
- **MonoSelect**, **FieldShell**, **MetadataCard**, **DccPicker** — selector và field chrome thống nhất
- **Elevation + surface tokens** (`elevation.py`, `surfaces.py`) — depth qua luminance, không hardcode hex rải rác
- **iOS switch** component; Settings dialog refactor theo design system
- Global QSS/tooltip/scrollbar cập nhật theo surface ladder

## Fusion comp preflight
- **Comp loader / saver I/O**, render paths, upstream render check
- **Preflight hub + dialog** — audit comp savers, upstream issues, apply plan trước khi render
- Fusion integration mở rộng (`dcc_fusion.py`, `comp_fusion_scripts.py`)

## Video / sequence
- **Sequence proxy** — PNG flipbook cache cho EXR/DPX/HDR nặng; worker nền + manifest
- Video preview: proxy build UX, playback backend cải thiện

## Integrations
- **Discord webhook channels** editor trong Settings — nhiều kênh, toggle sự kiện từng kênh
- `integrations_config` mở rộng validation + mask URL

## Pipeline UI
- Inspector comp/review blocks; main view comp preflight entry
- Shot review card cập nhật; popup position helpers mở rộng

## Tests
- Comp I/O, render paths, upstream check, preflight, elevation, surfaces, sequence proxy cache, integrations config

# MonoStudio 26.18.2

## Fusion comp preflight
- **Upstream frame repair** — phát hiện/sửa loader trỏ đúng version nhưng thiếu frame file
- **Disk extent scan** — kiểm tra phạm vi frame thực tế trên disk; apply summary rõ hơn cho wrong-entity
- **Comp saver repair** — sửa thiếu dấu phẩy `EndRenderScripts`, escape `EndRenderScript` bị hỏng
- Preflight dialog/hub: messaging và apply plan cải thiện

## Fusion Discord notify
- Ghi `python.path` + `notify.cmd` vào project — Fusion gọi Discord qua Python của MonoStudio (có log)

## Sequence preview
- Cải thiện resolve preview cho image sequence loaders

# MonoStudio 26 — Release

## Highlights

- **DCC (Windows only)**: Open file via `os.startfile` for Houdini, Maya, Substance Painter (no Python env conflict). RizomUV keeps Popen so .fbx always opens in configured RizomUV. Blender unchanged (--python-expr). All DCC adapters simplified for Windows.
- **Delete folder**: From card/DCC menu, delete DCC folder (or work folder). Warns if folder contains other subfolders; structured confirm dialog with section titles and full paths (mono, selectable). Open Folder on card opens **department folder**.
- **Card context menu**: Open icon = active DCC brand icon; Open opens active DCC only. Refresh icon = refresh-cw. Delete icon red (trash-2). Rule dialog for delete confirmation.

## Changes in this release

- fix: Houdini/Maya/Substance Painter — open file with os.startfile (avoids python313.dll conflict when launching from build)
- fix: RizomUV open_file keeps Popen(exe, path) so .fbx opens in configured RizomUV (no startfile)
- chore: DCC adapters Windows-only; remove _is_windows branches
- feat: Delete folder — remove DCC folder when use_dcc_folders; warn if other folders or work subfolders; structured DeleteFolderConfirmDialog (section titles + mono paths)
- feat: Card context — Open Folder opens department folder; Open action uses active DCC icon and opens active DCC only; Refresh icon refresh-cw; Delete icon red (trash-2)

## Install

Download **MonoStudio26_Setup.exe** from the Assets below and run. The installer will close the app if it is running so the update can be applied.

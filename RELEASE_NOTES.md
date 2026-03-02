# MonoStudio 26 — Release v26.2.1

## Highlights

- **Update check**: Download link built from tag + filename (`releases/download/{tag}/MonoStudio26_Setup.exe`) so download is reliable. Cache result 1 hour to avoid GitHub rate limit (60/h); optional `MONOSTUDIO_GITHUB_TOKEN` for 5000/h. Clear message when rate limit exceeded.
- **Update on startup**: Check runs after launch; cached result shown in Settings → Updates. Top bar update button with red dot, auto tooltip, opens Settings → Updates on click.
- **Download**: Retry with fallback URL if file invalid; Windows PowerShell fallback; validate installer (size, MZ header) before run. Friendly error if download or launch fails.
- **Build & release**: Auto-commit, version bump, publish via `publish_release.ps1`. Debug: `MONOSTUDIO_FAKE_UPDATE=1` to test update UI.

## Changes in this release

- fix: update checker — build download URL from tag + filename; cache 1h; token support; 403/rate limit message
- fix: download retry with fallback URL; PowerShell fallback; validate installer before run; show error in UI
- feat: update on startup, top bar update button, cached result in Settings
- chore: rule + doc for GitHub token (optional, free)

## Install

Download **MonoStudio26_Setup.exe** from the Assets below and run. The installer will close the app if it is running so the update can be applied.

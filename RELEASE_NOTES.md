# MonoStudio 26 — Release v26.1.1

## Highlights

- **In-app update**: Check for updates from Settings → General → Updates (GitHub Releases). Version display with Major · Minor · Patch.
- **Build & release**: Auto-commit before build, version bump from commit type (fix → patch, feat/style/docs → minor). Publish to GitHub via `publish_release.ps1` (GitHub CLI).
- **Settings → Updates**: Redesigned UI (version card, release notes, “Latest on GitHub” hint). Release notes always shown after check (even when up to date).
- **Pipeline**: Scan rules tab with Publish ignore extensions (.tmp, .bak, .mtl, …). Inbox improvements (split view, distribute from tree, History dialog). Splash screen and version in sidebar.

## Changes in this release

- chore: prepare build
- feat: ui update (Updates tab, version card, release notes)
- fix: fixes (core/ui) — update checker, CheckResult, error messages
- feat(settings): Scan rules tab with Publish ignore extensions
- feat: splash polish, version module, sidebar version, icons, notifications, tags
- feat: add splash screen with loading progress and git version
- feat: Inbox/Outbox, thumbnails paste, icons, build & installer setup
- feat(inbox): remove mapping view, distribute from tree, History dialog
- feat(inbox): add Inbox page (core + sidebar + main view)
- UI: borderless/resize plans, Lucide icons, style updates

## Install

Download **MonoStudio26_Setup.exe** from the Assets below and run. The installer will close the app if it is running so the update can be applied.

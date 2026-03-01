# MonoStudio 26 — Release v26.1.5

## Highlights

- **In-app update**: Check for updates from Settings → General → Updates (GitHub Releases). Version display with Major · Minor · Patch. **Download fix**: follow redirects manually and send User-Agent on every request so GitHub CDN accepts the installer download; Updates tab layout (version card beside check/actions, notes area expands); release notes line spacing (165%).
- **Build & release**: Auto-commit before build, version bump from commit type (fix → patch, feat/style/docs → minor). Publish to GitHub via `publish_release.ps1`; release notes from RELEASE_NOTES.md or auto-generated from git log.
- **Settings → Updates**: Version card, “Latest on GitHub” hint, release notes always shown after check. Clear errors when GITHUB_REPO not set or repo/release not found.
- **Pipeline**: Scan rules (Publish ignore extensions). Inbox (split view, distribute from tree, History dialog). Splash screen, version in sidebar.

## Changes in this release

- fix: update checker — manual redirect handling so User-Agent is sent to GitHub CDN; download no longer fails
- fix: Settings Updates tab — version card beside check/actions; release notes area expands; line spacing 165%
- chore: prepare build
- chore: prepare build
- feat: ui update (Updates tab, version card, release notes)
- fix: fixes (core/ui) — CheckResult, release notes when up to date
- feat(settings): Scan rules tab with Publish ignore extensions
- feat: splash polish, version module, sidebar version, icons, notifications, tags
- feat: add splash screen with loading progress and git version
- feat: Inbox/Outbox, thumbnails paste, icons, build & installer setup
- feat(inbox): remove mapping view, distribute from tree, History dialog
- feat(inbox): add Inbox page (core + sidebar + main view)

## Install

Download **MonoStudio26_Setup.exe** from the Assets below and run. The installer will close the app if it is running so the update can be applied.

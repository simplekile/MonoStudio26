# MonoStudio 26 — Release v26.1.7

## Highlights

- **Update on startup**: App checks for updates shortly after launch; result is cached so Settings → Updates shows it without clicking "Check for updates" again.
- **Top bar update button**: New button (download icon) next to the notifications bell. Red dot when an update is available; tooltip auto-shows for 5 seconds at startup if update available; click opens Settings → General → Updates.
- **In-app update**: Check for updates from Settings → General → Updates (GitHub Releases). Download fix (manual redirect + User-Agent for GitHub CDN); version card beside check/actions; release notes line spacing 165%.
- **Debug**: Set `MONOSTUDIO_FAKE_UPDATE=1` to simulate "update available" for testing the update UI.
- **Build & release**: Auto-commit before build, version bump from commit type. Publish via `publish_release.ps1`; release notes from RELEASE_NOTES.md.
- **Pipeline**: Scan rules, Inbox, splash, version in sidebar.

## Changes in this release

- feat: update check on startup; top bar update button with red dot, auto tooltip, open to Settings → Updates
- feat: cached update result so Settings → Updates shows startup check without re-checking
- fix: update checker — manual redirect + User-Agent for GitHub CDN; download no longer fails
- fix: Settings Updates tab — version card beside check/actions; notes area expands; line spacing 165%
- chore: debug fake update (MONOSTUDIO_FAKE_UPDATE=1)
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

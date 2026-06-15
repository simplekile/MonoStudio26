MonoStudio — bundled libmpv (optional at build time)
====================================================

Place portable libmpv Windows x64 files here before running build_installer.ps1:

  mpv-2.dll
  (+ sibling DLLs from the same folder)

Source: https://sourceforge.net/projects/mpv-player-windows/files/libmpv/
  e.g. extract mpv-dev-x86_64-*.7z — copy libmpv-2.dll as mpv-2.dll plus any sibling *.dll.

build_installer.ps1 copies this folder to dist/MonoStudio26/tools/mpv/ (next to _internal).

End users without a bundled copy can install via Settings → Updates → libmpv (Get → Install).

License: mpv/libmpv is GPL — see mpv license when redistributing binaries.

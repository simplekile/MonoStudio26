# Build MonoStudio 26: PyInstaller (onedir) then optional Inno Setup installer
# Prereqs: pip install PySide6 pyinstaller; Inno Setup 6 (optional, for .exe installer)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot

# 0) Generate app.ico from logo.svg (for EXE + installer icon)
Write-Host "Generating app.ico from logo..."
Set-Location $root
python build_icon.py
if ($LASTEXITCODE -ne 0) { Write-Warning "build_icon.py failed; EXE/installer will have no custom icon." }

# 1) PyInstaller onedir (--clean so EXE icon is embedded from current app.ico)
Write-Host "Building PyInstaller onedir (dist/MonoStudio26/)..."
python -m PyInstaller --clean --noconfirm monostudio26.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 2) Optional: Inno Setup (if iscc is in PATH)
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if ($iscc) {
    Write-Host "Building installer with Inno Setup..."
    & iscc "installer\MonoStudio26.iss"
} else {
    Write-Host "Inno Setup (iscc) not in PATH. Run installer\MonoStudio26.iss manually in Inno Setup Compiler, or add iscc to PATH."
}

Write-Host "Done. Run: dist\MonoStudio26\MonoStudio26.exe"

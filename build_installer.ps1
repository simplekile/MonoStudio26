# Build MonoStudio 26: PyInstaller (onedir) then optional Inno Setup installer
# Prereqs: pip install PySide6 pyinstaller; Inno Setup 6 (optional, for .exe installer)
# Optional: -NoCommit to skip auto-commit (default: commit uncommitted changes before build)

param([switch]$NoCommit)

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# 0) Commit xong mới build: nếu có thay đổi chưa commit thì tự commit (message ước lượng từ diff)
if (-not $NoCommit) {
    $status = git status --porcelain 2>$null
    if ($status) {
        Write-Host "Uncommitted changes detected. Estimating change type..."
        git add -A
        # Ước lượng type (patch/fix vs minor/feat) từ file thay đổi và nội dung diff
        $fileList = @(git diff --cached --name-only 2>$null)
        $files = $fileList -join " "
        $diffText = (git diff --cached -U0 2>$null | Select-Object -First 100) -join " "
        $isFix = $diffText -match "\b(fix|bug|error|correct|repair|resolve)\b" -or $files -match "fix_|bugfix|patch"
        $hasCore = $files -match "monostudio[\\/]core[\\/]"
        $hasUI = $files -match "monostudio[\\/]ui_qt[\\/]"
        $pyFiles = $fileList | Where-Object { $_ -match "\.py$" }
        $onlyStylePy = ($pyFiles.Count -gt 0) -and (($pyFiles | Where-Object { $_ -match "style\.py$" }).Count -eq $pyFiles.Count)
        $onlyDocs = ($fileList.Count -gt 0) -and (($fileList | Where-Object { $_ -match "\.(md[c]?|md)$|docs?[\\/]" }).Count -eq $fileList.Count)
        $onlyBuild = ($fileList.Count -gt 0) -and (($fileList | Where-Object { $_ -match "build_|\.spec|installer[\\/]" }).Count -eq $fileList.Count)
        $type = "chore"
        $hint = "prepare build"
        if ($onlyBuild) { $type = "chore"; $hint = "build config" }
        elseif ($onlyDocs) { $type = "docs"; $hint = "update docs" }
        elseif ($onlyStylePy) { $type = "style"; $hint = "update styles" }
        elseif ($isFix -and ($hasCore -or $hasUI)) { $type = "fix"; $hint = "fixes (core/ui)" }
        elseif ($hasUI) { $type = "feat"; $hint = "ui update" }
        elseif ($hasCore) { $type = "feat"; $hint = "core update" }
        $msg = "${type}: ${hint}"
        Write-Host "Commit message: $msg"
        git commit -m $msg
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        Write-Host "Committed. Proceeding with build."
    } else {
        Write-Host "Working tree clean. Proceeding with build."
    }
}

# 2) Generate app.ico from logo.svg (for EXE + installer icon)
Write-Host "Generating app.ico from logo..."
python build_icon.py
if ($LASTEXITCODE -ne 0) { Write-Warning "build_icon.py failed; EXE/installer will have no custom icon." }

# 3) Write VERSION from git commit count (v26.<count> -> baked into build + installer)
Write-Host "Writing VERSION from git..."
python build_version.py
if ($LASTEXITCODE -ne 0) { Write-Warning "build_version.py failed; version may be v26 only." }

# 4) PyInstaller onedir (--clean so EXE icon is embedded from current app.ico)
Write-Host "Building PyInstaller onedir (dist/MonoStudio26/)..."
python -m PyInstaller --clean --noconfirm monostudio26.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# 5) Optional: Inno Setup (iscc in PATH or default install path)
$isccExe = $null
if (Get-Command iscc -ErrorAction SilentlyContinue) { $isccExe = "iscc" }
elseif (Test-Path "C:\Program Files (x86)\Inno Setup 6\ISCC.exe") { $isccExe = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" }
elseif (Test-Path "C:\Program Files\Inno Setup 6\ISCC.exe") { $isccExe = "C:\Program Files\Inno Setup 6\ISCC.exe" }
if ($isccExe) {
    # Pass version to Inno: 26.minor.patch from monostudio_data\VERSION (e.g. 26.1.2 or legacy 26.24 -> 26.0.24)
    $verFile = Join-Path $root "monostudio_data\VERSION"
    $appVer = "26.0.0"
    if (Test-Path $verFile) {
        $verText = (Get-Content $verFile -Raw).Trim() -replace "^\s*v", ""
        if ($verText -match "^26\.(\d+)\.(\d+)$") { $appVer = "26.$($Matches[1]).$($Matches[2])" }
        elseif ($verText -match "^26\.(\d+)$") { $appVer = "26.0.$($Matches[1])" }
    }
    Write-Host "Building installer with Inno Setup (version $appVer)..."
    & $isccExe "/DMyAppVersion=$appVer" "installer\MonoStudio26.iss"
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} else {
    Write-Host "Inno Setup not found. Install from https://jrsoftware.org/isinfo.php then re-run, or open installer\MonoStudio26.iss in Inno Setup Compiler."
}

Write-Host "Done. Run: dist\MonoStudio26\MonoStudio26.exe"
Write-Host "Publish to GitHub (optional): .\publish_release.ps1  (can: winget install GitHub.cli  va  gh auth login)"

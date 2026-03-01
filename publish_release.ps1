# Publish release lên GitHub sau khi build (dùng GitHub CLI).
# Lần đầu: cài gh (winget install GitHub.cli) rồi chạy: gh auth login
# Sau đó: .\build_installer.ps1  rồi  .\publish_release.ps1

$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
Set-Location $root

# Tag từ VERSION (vd. 26.1.0 -> v26.1.0)
$verFile = Join-Path $root "monostudio_data\VERSION"
if (-not (Test-Path $verFile)) {
    Write-Error "VERSION not found. Run build_installer.ps1 first."
}
$verText = (Get-Content $verFile -Raw).Trim() -replace "^\s*v", ""
$tag = if ($verText -match "^\d") { "v$verText" } else { $verText }

$exe = Join-Path $root "dist\MonoStudio26_Setup.exe"
if (-not (Test-Path $exe)) {
    Write-Error "Installer not found: $exe. Run build_installer.ps1 first."
}

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    Write-Host "GitHub CLI (gh) chua cai. Cai: winget install GitHub.cli"
    Write-Host "Sau do dang nhap: gh auth login"
    exit 1
}

$auth = gh auth status 2>&1
if ($LASTEXITCODE -ne 0) {
    Write-Host "Chua dang nhap GitHub. Chay: gh auth login"
    exit 1
}

Write-Host "Publishing release $tag to GitHub..."
Write-Host "  Tag: $tag"
Write-Host "  File: $exe"
$notes = "Release $tag. See commit history for changes."
gh release create $tag $exe --title $tag --notes $notes
if ($LASTEXITCODE -ne 0) {
    Write-Host "Co the tag $tag da ton tai. Xoa hoac dung version moi: gh release delete $tag"
    exit $LASTEXITCODE
}
Write-Host "Done. Release: https://github.com/$(gh repo view --json nameWithOwner -q .nameWithOwner)/releases/tag/$tag"

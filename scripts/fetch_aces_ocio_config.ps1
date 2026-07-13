# Download ACES 1.3 cg-config (OCIO v2.1) into monostudio_data/ocio/aces_1.3/
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Dest = Join-Path $Root "monostudio_data\ocio\aces_1.3"
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
$Asset = "cg-config-v1.0.0-rc2_aces-v1.3_ocio-v2.1.ocio"
gh release download v1.0.0-rc2 `
    --repo AcademySoftwareFoundation/OpenColorIO-Config-ACES `
    --pattern $Asset `
    --dir $Dest
Copy-Item (Join-Path $Dest $Asset) (Join-Path $Dest "config.ocio") -Force
Write-Host "OCIO config installed to $Dest\config.ocio"

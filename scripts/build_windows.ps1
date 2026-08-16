param(
    [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

if (-not $SkipInstall) {
    python -m pip install ".[build]"
}

python -m PyInstaller `
    --noconfirm `
    --clean `
    --windowed `
    --name PinForge `
    --collect-all PySide6 `
    --collect-all keyring `
    --collect-data pinforge `
    src/pinforge/ui/app.py

if (Test-Path "PinForge-Windows.zip") {
    Remove-Item "PinForge-Windows.zip"
}
Compress-Archive -Path "dist/PinForge/*" -DestinationPath "PinForge-Windows.zip"

Write-Host "Created PinForge-Windows.zip"

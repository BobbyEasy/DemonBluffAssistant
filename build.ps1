param(
    [ValidatePattern('^[A-Za-z0-9._-]+$')]
    [string]$Name = "DemonBluffAssistant"
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw ".venv not found. Run setup.ps1 first."
}

Set-Location -LiteralPath $Root
$running = Get-Process -Name $Name -ErrorAction SilentlyContinue
if ($running) {
    throw "$Name is running. Close it before rebuilding."
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name $Name `
    --paths src `
    --collect-all z3 `
    --collect-all rapidocr `
    --collect-all onnxruntime `
    --collect-submodules uvicorn `
    --add-data "src\demon_bluff_assistant\static;demon_bluff_assistant\static" `
    --add-data "src\demon_bluff_assistant\role_catalog.json;demon_bluff_assistant" `
    src\demon_bluff_assistant\main.py

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --noconsole `
    --name StopDemonBluffAssistant `
    --paths src `
    src\demon_bluff_assistant\stop_assistant.py

if ($LASTEXITCODE -ne 0) {
    throw "Stop tool build failed with exit code $LASTEXITCODE."
}

Write-Host "Build complete: dist\$Name.exe" -ForegroundColor Green
Write-Host "Stop tool complete: dist\StopDemonBluffAssistant.exe" -ForegroundColor Green

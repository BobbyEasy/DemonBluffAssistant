$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    throw ".venv not found. Run setup.ps1 first."
}

$env:PYTHONPATH = Join-Path $Root "src"
Set-Location -LiteralPath $Root
& $Python -m demon_bluff_assistant.main

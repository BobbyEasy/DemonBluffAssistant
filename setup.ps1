$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $VenvPython)) {
    $Python = Get-Command py -ErrorAction SilentlyContinue
    if ($Python) {
        & py -3.12 -m venv (Join-Path $Root ".venv")
    } else {
        $Python = Get-Command python -ErrorAction Stop
        & $Python.Source -m venv (Join-Path $Root ".venv")
    }
}

& $VenvPython -m pip install --disable-pip-version-check -e "${Root}[dev]"
Write-Host "Environment ready. Run .\start.ps1 to launch." -ForegroundColor Green

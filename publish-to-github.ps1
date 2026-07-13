param(
    [string]$Owner = "BobbyEasy",
    [string]$Repository = "DemonBluffAssistant",
    [ValidateSet("public", "private")]
    [string]$Visibility
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $Root

if (-not $Visibility) {
    $Visibility = Read-Host "Repository visibility (public/private)"
}
if ($Visibility -notin @("public", "private")) {
    throw "Visibility must be public or private."
}
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    throw "Git is required. Install Git for Windows and retry."
}
if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw "GitHub CLI is required. Install it, run 'gh auth login', and retry."
}

& gh auth status
if ($LASTEXITCODE -ne 0) {
    throw "GitHub CLI is not authenticated. Run 'gh auth login' and retry."
}

$forbidden = @("data", "dist", "build", ".venv", ".env")
foreach ($name in $forbidden) {
    if (Test-Path -LiteralPath (Join-Path $Root $name)) {
        throw "Refusing to publish because forbidden local path exists: $name"
    }
}

$files = Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
    $_.FullName -notlike (Join-Path $Root ".git\*")
}
$privateExtensions = @(".png", ".jpg", ".jpeg", ".log", ".key", ".pem")
$privateFiles = $files | Where-Object { $_.Extension -in $privateExtensions }
if ($privateFiles) {
    throw "Refusing to publish private or binary files: $($privateFiles.FullName -join ', ')"
}

$sensitivePatterns = @(
    ("C:" + "\\Users\\"),
    ("s" + "k-[A-Za-z0-9_-]{16,}"),
    ("gh" + "p_[A-Za-z0-9]{20,}"),
    ("github" + "_pat_[A-Za-z0-9_]{20,}"),
    ("AK" + "IA[0-9A-Z]{16}"),
    ("BEGIN " + ".*PRIVATE KEY")
)
foreach ($pattern in $sensitivePatterns) {
    $match = $files | Select-String -Pattern $pattern -List
    if ($match) {
        throw "Refusing to publish a possible secret or personal path: $($match.Path -join ', ')"
    }
}

if (-not (Test-Path -LiteralPath (Join-Path $Root ".git"))) {
    & git init -b main
    if ($LASTEXITCODE -ne 0) { throw "git init failed." }
}

& git add --all
if ($LASTEXITCODE -ne 0) { throw "git add failed." }

& git rev-parse --verify HEAD 2>$null
$hasCommit = $LASTEXITCODE -eq 0
& git diff --cached --quiet
$hasStagedChanges = $LASTEXITCODE -ne 0
if (-not $hasCommit) {
    & git commit -m "Initial commit"
    if ($LASTEXITCODE -ne 0) { throw "Initial commit failed." }
} elseif ($hasStagedChanges) {
    & git commit -m "Update sanitized release"
    if ($LASTEXITCODE -ne 0) { throw "Commit failed." }
}

$target = "https://github.com/$Owner/$Repository.git"
$origin = (& git remote get-url origin 2>$null)
if ($LASTEXITCODE -eq 0 -and $origin -ne $target) {
    throw "origin points to '$origin', not '$target'. Refusing to overwrite it."
}

& gh repo view "$Owner/$Repository" --json name 1>$null 2>$null
if ($LASTEXITCODE -ne 0) {
    & gh repo create "$Owner/$Repository" "--$Visibility" --source . --remote origin
    if ($LASTEXITCODE -ne 0) { throw "GitHub repository creation failed." }
} elseif (-not $origin) {
    & git remote add origin $target
    if ($LASTEXITCODE -ne 0) { throw "Adding origin failed." }
}

& git push -u origin main
if ($LASTEXITCODE -ne 0) {
    throw "Push was rejected. The script never force-pushes; inspect the remote history."
}

Write-Host "Published: https://github.com/$Owner/$Repository" -ForegroundColor Green

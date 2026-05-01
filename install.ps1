<#
.SYNOPSIS
  One-shot installer for spotify-video-combiner on Windows.

.DESCRIPTION
  Bootstraps a self-contained Python virtual environment in `.venv` next to
  this script, installs the package (which pulls zotify in as a dependency),
  and verifies that ffmpeg is on PATH (offering to install it via winget if
  not). After this finishes, you can run `svc-gui` for the GUI or `svc` for
  the CLI from the activated venv.

.NOTES
  Requirements: Python 3.11 or newer must be on PATH (we don't install Python
  for you because that almost always wants user input about PATH placement,
  Store vs traditional installer, etc.). If you don't have Python, install it
  from https://www.python.org/downloads/ first.

.EXAMPLE
  .\install.ps1

.EXAMPLE
  # If your execution policy blocks scripts:
  powershell -ExecutionPolicy Bypass -File .\install.ps1
#>

[CmdletBinding()]
param(
    [string]$VenvPath = ".venv",
    [switch]$SkipFfmpegPrompt
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Write-Step([string]$msg) { Write-Host "==> $msg" -ForegroundColor Cyan }
function Write-Ok([string]$msg)   { Write-Host "    $msg" -ForegroundColor Green }
function Write-Warn2([string]$msg) { Write-Host "    $msg" -ForegroundColor Yellow }
function Write-Err2([string]$msg)  { Write-Host "    $msg" -ForegroundColor Red }

# --- 1. Find a usable Python ------------------------------------------------
Write-Step "Locating Python 3.11+"
$python = $null
foreach ($candidate in @("py -3.12", "py -3.11", "py -3", "python", "python3")) {
    $parts = $candidate -split " "
    $cmd = Get-Command $parts[0] -ErrorAction SilentlyContinue
    if (-not $cmd) { continue }
    $verJson = & $parts[0] $parts[1..($parts.Length - 1)] -c "import sys, json; print(json.dumps(sys.version_info[:3]))" 2>$null
    if (-not $verJson) { continue }
    $version = ($verJson | ConvertFrom-Json)
    if ($version[0] -gt 3 -or ($version[0] -eq 3 -and $version[1] -ge 11)) {
        $python = $candidate
        Write-Ok "Found $candidate -> Python $($version -join '.')"
        break
    }
}

if (-not $python) {
    Write-Err2 "No suitable Python found. Install Python 3.11+ from https://www.python.org/downloads/ (be sure to check 'Add to PATH'), then re-run this installer."
    exit 1
}

# --- 2. Create the virtualenv ----------------------------------------------
Write-Step "Creating virtual environment in $VenvPath"
if (Test-Path $VenvPath) {
    Write-Ok "Reusing existing $VenvPath"
} else {
    $pyParts = $python -split " "
    & $pyParts[0] @($pyParts[1..($pyParts.Length - 1)]) -m venv $VenvPath
    if ($LASTEXITCODE -ne 0) { throw "Failed to create venv" }
    Write-Ok "Created $VenvPath"
}

$venvPython = Join-Path -Path $VenvPath -ChildPath "Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Err2 "Expected $venvPython but it doesn't exist."
    exit 1
}

# --- 3. Install package + deps ---------------------------------------------
Write-Step "Upgrading pip"
& $venvPython -m pip install --upgrade pip --quiet
if ($LASTEXITCODE -ne 0) { throw "pip upgrade failed" }
Write-Ok "pip is current"

Write-Step "Installing spotify-video-combiner (this also installs zotify)"
Write-Warn2 "First-time install pulls librespot + protobuf + zotify; expect ~30-60s."
& $venvPython -m pip install -e . --upgrade
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Write-Ok "Package installed"

# --- 4. ffmpeg check --------------------------------------------------------
Write-Step "Checking for ffmpeg"
$ffmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($ffmpeg) {
    Write-Ok "ffmpeg found at $($ffmpeg.Source)"
} else {
    Write-Warn2 "ffmpeg not found on PATH."
    $hasWinget = Get-Command winget -ErrorAction SilentlyContinue
    if ($hasWinget -and -not $SkipFfmpegPrompt) {
        $answer = Read-Host "Install ffmpeg now via winget? [Y/n]"
        if ($answer -eq "" -or $answer -match "^[Yy]") {
            winget install --id Gyan.FFmpeg --silent --accept-source-agreements --accept-package-agreements
            if ($LASTEXITCODE -eq 0) {
                Write-Ok "ffmpeg installed via winget. You may need to open a new shell for PATH to update."
            } else {
                Write-Err2 "winget install failed. Install ffmpeg manually from https://ffmpeg.org/."
            }
        } else {
            Write-Warn2 "Skipped. Install ffmpeg manually from https://ffmpeg.org/ before running svc."
        }
    } else {
        Write-Warn2 "winget is unavailable. Install ffmpeg manually from https://ffmpeg.org/ before running svc."
    }
}

# --- 5. Done ---------------------------------------------------------------
Write-Step "Install complete."
Write-Host ""
Write-Host "Activate the venv:" -ForegroundColor White
Write-Host "  . $VenvPath\Scripts\Activate.ps1"
Write-Host ""
Write-Host "Then launch the GUI:" -ForegroundColor White
Write-Host "  svc-gui"
Write-Host ""
Write-Host "Or use the CLI directly:" -ForegroundColor White
Write-Host "  svc all https://open.spotify.com/playlist/<id>"
Write-Host ""
Write-Host "First-time: a Spotify Web API credentials template will be created" -ForegroundColor White
Write-Host "in %APPDATA%\spotify-video-combiner\credentials.env. Fill it in once."

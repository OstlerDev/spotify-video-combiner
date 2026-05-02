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
function Test-IsWindowsAppStub {
    # The Microsoft Store ships a 0-byte `python.exe` redirector at
    # %LocalAppData%\Microsoft\WindowsApps\python.exe. Running it with no
    # Python installed opens the Store and blocks waiting for the user to
    # finish the install dialog -- which would *actually* hang our script.
    # Detect it by path and skip.
    param([string]$path)
    if (-not $path) { return $false }
    return ($path -like "*\Microsoft\WindowsApps\*")
}

function Resolve-Python {
    # Probe each candidate; the loop must NOT halt under
    # ErrorActionPreference=Stop when an early candidate fails (py.exe
    # writes "No suitable Python runtime found" to stderr when no Python
    # is registered with the launcher, and we want to fall through to
    # plain `python` cleanly).
    $previousEAP = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        foreach ($candidate in @("py -3", "python", "python3")) {
            $parts = $candidate -split " "
            $cmdInfo = Get-Command $parts[0] -ErrorAction SilentlyContinue
            if (-not $cmdInfo) { continue }
            if (Test-IsWindowsAppStub $cmdInfo.Source) {
                Write-Warn2 "Skipping $($parts[0]) (Microsoft Store stub at $($cmdInfo.Source))"
                continue
            }
            # CRITICAL: $extra MUST be an array, not a scalar. Splatting a
            # scalar string with `@var` iterates over its CHARACTERS, so
            # `py -3` would be invoked as `py "-" "3" -c ...`. py.exe
            # interprets a bare "-" as "read script from stdin" and
            # blocks forever waiting on input.
            #
            # `Select-Object -Skip 1` followed by an outer `@(...)` is the
            # only reliable PowerShell incantation here: a bare range
            # slice unwraps single-element results to a scalar, and even
            # `@(if (...) { ... })` doesn't re-wrap because the `if`
            # expression unwraps before `@()` sees the value.
            $extra = @($parts | Select-Object -Skip 1)
            $verJson = & $parts[0] @extra -c "import sys, json; print(json.dumps(sys.version_info[:3]))" 2>$null
            if ($LASTEXITCODE -ne 0 -or -not $verJson) { continue }
            $version = $verJson | ConvertFrom-Json -ErrorAction SilentlyContinue
            if (-not $version) { continue }
            if ($version[0] -gt 3 -or ($version[0] -eq 3 -and $version[1] -ge 11)) {
                Write-Ok "Found $candidate -> Python $($version -join '.')"
                return $candidate
            } else {
                Write-Warn2 "Skipping $candidate (Python $($version -join '.') is too old; need 3.11+)"
            }
        }
        return $null
    } finally {
        $ErrorActionPreference = $previousEAP
    }
}

Write-Step "Locating Python 3.11+"
$python = Resolve-Python

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
    # See note in Resolve-Python: must use `@(... | Select-Object -Skip 1)`
    # so a single trailing arg like `-3` doesn't unwrap to a scalar and get
    # splatted character-by-character into the child invocation.
    $pyExtra = @($pyParts | Select-Object -Skip 1)
    & $pyParts[0] @pyExtra -m venv $VenvPath
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
Write-Warn2 "First-time install clones librespot + zotify from GitHub; allow 1-3 minutes."
Write-Warn2 "If output appears to pause, pip is waiting on git -- not hung."
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
Write-Host "  svc signin                                          # one-time browser sign-in"
Write-Host "  svc all https://open.spotify.com/playlist/<id>"
Write-Host ""
Write-Host "First-time: clicking 'Sign In' (or running 'svc signin') opens" -ForegroundColor White
Write-Host "your browser to authorise this app with your Spotify account."

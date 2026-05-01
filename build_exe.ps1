<#
.SYNOPSIS
  Build a single-file Windows .exe of the GUI using PyInstaller.

.DESCRIPTION
  Bootstraps a venv (if needed), installs dev/build deps + this package,
  downloads a static ffmpeg build into ./build/ffmpeg.exe, and runs
  PyInstaller against svc.spec. The result is dist\svc-gui.exe - a single
  file, no Python required to run.

  ffmpeg static build comes from https://www.gyan.dev/ffmpeg/builds/ (Gyan
  is the de-facto Windows distributor recommended on the official ffmpeg
  download page). Cached under build/ between runs.

.EXAMPLE
  .\build_exe.ps1            # full build (downloads ffmpeg if missing)

.EXAMPLE
  .\build_exe.ps1 -Clean     # clean dist/build first
#>

[CmdletBinding()]
param(
    [string]$VenvPath = ".venv",
    [switch]$Clean,
    [string]$FfmpegUrl = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

function Write-Step([string]$m) { Write-Host "==> $m" -ForegroundColor Cyan }
function Write-Ok([string]$m)   { Write-Host "    $m" -ForegroundColor Green }
function Write-Warn2([string]$m){ Write-Host "    $m" -ForegroundColor Yellow }

if (-not (Test-Path $VenvPath)) {
    Write-Step "No venv at $VenvPath - running install.ps1 first"
    & "$PSScriptRoot\install.ps1" -VenvPath $VenvPath -SkipFfmpegPrompt
}

$venvPython = Join-Path -Path $VenvPath -ChildPath "Scripts\python.exe"

Write-Step "Installing build dependencies (pyinstaller)"
& $venvPython -m pip install --quiet --upgrade pip
& $venvPython -m pip install --quiet -e ".[build]"
if ($LASTEXITCODE -ne 0) { throw "pip install failed" }
Write-Ok "build deps ready"

if ($Clean) {
    Write-Step "Cleaning dist/ and build/"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue dist, build
}

# --- Download ffmpeg if missing -------------------------------------------
$buildDir = Join-Path $PSScriptRoot "build"
$ffmpegBin = Join-Path $buildDir "ffmpeg.exe"
$ffmpegZip = Join-Path $buildDir "ffmpeg-release.zip"

if (-not (Test-Path $ffmpegBin)) {
    Write-Step "Downloading ffmpeg static build"
    New-Item -ItemType Directory -Force -Path $buildDir | Out-Null
    Invoke-WebRequest -Uri $FfmpegUrl -OutFile $ffmpegZip -UseBasicParsing
    Write-Ok "Downloaded $(([math]::Round((Get-Item $ffmpegZip).Length / 1MB, 1))) MB"

    Write-Step "Extracting ffmpeg.exe from zip"
    $extractDir = Join-Path $buildDir "ffmpeg-extract"
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $extractDir
    Expand-Archive -Path $ffmpegZip -DestinationPath $extractDir
    $found = Get-ChildItem -Recurse -Path $extractDir -Filter "ffmpeg.exe" | Select-Object -First 1
    if (-not $found) { throw "ffmpeg.exe not found in downloaded archive" }
    Copy-Item -LiteralPath $found.FullName -Destination $ffmpegBin -Force
    Remove-Item -Recurse -Force $extractDir, $ffmpegZip
    Write-Ok "ffmpeg.exe staged at $ffmpegBin"
} else {
    Write-Ok "Using cached ffmpeg.exe at $ffmpegBin"
}

# --- Run PyInstaller -------------------------------------------------------
Write-Step "Running PyInstaller (this takes 1-3 minutes)"
# --onefile is already declared in the .spec; passing it on the CLI when a
# spec file is supplied is rejected by PyInstaller.
& $venvPython -m PyInstaller --noconfirm svc.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$outExe = Join-Path $PSScriptRoot "dist\svc-gui.exe"
if (-not (Test-Path $outExe)) { throw "PyInstaller succeeded but $outExe is missing" }
$sizeMb = [math]::Round((Get-Item $outExe).Length / 1MB, 1)

Write-Step "Build complete."
Write-Host ""
Write-Ok "Output: $outExe ($sizeMb MB)"
Write-Host ""
Write-Host "Distribute this single .exe - no Python install required to run it." -ForegroundColor White
Write-Warn2 "Note: first launch unpacks ~$sizeMb MB to %TEMP%, so cold-start takes a few seconds."

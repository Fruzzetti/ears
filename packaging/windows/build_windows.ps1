# ==============================================================================
# EARS -- Self-Contained Windows Distribution & Installer Builder
# Bundles Python 3.11 Embeddable + FFmpeg + Ears into `dist\Ears_Windows_Portable`
# and compiles `dist\Ears_Setup.exe` if Inno Setup (`ISCC.exe`) is installed.
# ==============================================================================
$ErrorActionPreference = "Stop"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectRoot = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$DistDir = Join-Path $ProjectRoot "dist\Ears_Windows_Portable"
$RuntimeDir = Join-Path $DistDir "runtime"

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  EARS -- WINDOWS STANDALONE BUNDLE & INSTALLER BUILDER" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan

New-Item -ItemType Directory -Force -Path $DistDir, $RuntimeDir | Out-Null

# 1. Copy Ears Application Files
Write-Host "[1/4] Copying Ears application source and static assets..." -ForegroundColor Green
Copy-Item -Path (Join-Path $ProjectRoot "ears.py") -Destination $DistDir -Force
Copy-Item -Path (Join-Path $ProjectRoot "requirements.txt") -Destination $DistDir -Force
Copy-Item -Path (Join-Path $ProjectRoot "install_windows.bat") -Destination $DistDir -Force
Copy-Item -Path (Join-Path $ProjectRoot "uninstall_windows.bat") -Destination $DistDir -Force
Copy-Item -Path (Join-Path $ProjectRoot "backend") -Destination (Join-Path $DistDir "backend") -Recurse -Force
Copy-Item -Path (Join-Path $ProjectRoot "static") -Destination (Join-Path $DistDir "static") -Recurse -Force
New-Item -ItemType Directory -Force -Path (Join-Path $DistDir "packaging\windows") | Out-Null
Copy-Item -Path (Join-Path $ScriptDir "install_windows.ps1") -Destination (Join-Path $DistDir "packaging\windows") -Force
Copy-Item -Path (Join-Path $ScriptDir "uninstall_windows.ps1") -Destination (Join-Path $DistDir "packaging\windows") -Force

# 2. Download Python 3.11 Embeddable Runtime if not cached
$PyDir = Join-Path $RuntimeDir "python"
if (-not (Test-Path (Join-Path $PyDir "python.exe"))) {
    Write-Host "[2/4] Downloading Python 3.11.9 Embeddable Runtime (amd64)..." -ForegroundColor Green
    New-Item -ItemType Directory -Force -Path $PyDir | Out-Null
    $PyZip = Join-Path $RuntimeDir "python-embed.zip"
    Invoke-WebRequest -Uri "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip" -OutFile $PyZip
    Expand-Archive -Path $PyZip -DestinationPath $PyDir -Force
    Remove-Item $PyZip -Force

    $PthFile = Join-Path $PyDir "python311._pth"
    (Get-Content $PthFile) -replace "#import site", "import site" | Set-Content $PthFile

    $GetPip = Join-Path $RuntimeDir "get-pip.py"
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip
    & (Join-Path $PyDir "python.exe") $GetPip --no-warn-script-location
    Remove-Item $GetPip -Force

    & (Join-Path $PyDir "Scripts\pip.exe") install --no-warn-script-location -r (Join-Path $ProjectRoot "requirements.txt")
} else {
    Write-Host "[2/4] Reusing cached Python Embeddable Runtime..." -ForegroundColor Green
}

# 3. Bundle FFmpeg binary if available on build host
$FfmpegDest = Join-Path $RuntimeDir "ffmpeg"
New-Item -ItemType Directory -Force -Path $FfmpegDest | Out-Null
$HostFfmpeg = Get-Command ffmpeg -ErrorAction SilentlyContinue
if ($HostFfmpeg) {
    Write-Host "[3/4] Bundling ffmpeg.exe from host..." -ForegroundColor Green
    Copy-Item -Path $HostFfmpeg.Source -Destination (Join-Path $FfmpegDest "ffmpeg.exe") -Force
} else {
    Write-Host "[3/4] Note: ffmpeg.exe not in host PATH (installer will fetch via winget if needed)." -ForegroundColor Yellow
}

# Create portable launcher
$PortableVbs = Join-Path $DistDir "Ears.vbs"
$PortableVbsLines = @(
    'Set WshShell = CreateObject("WScript.Shell")',
    'WshShell.CurrentDirectory = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)',
    'Set Env = WshShell.Environment("PROCESS")',
    'Env("PATH") = WshShell.CurrentDirectory & "\runtime\ffmpeg;" & WshShell.CurrentDirectory & "\runtime\python;" & Env("PATH")',
    'WshShell.Run """" & WshShell.CurrentDirectory & "\runtime\python\pythonw.exe"" """ & WshShell.CurrentDirectory & "\ears.py"" --tray --open-browser", 0, False'
)
Set-Content -Path $PortableVbs -Value $PortableVbsLines -Encoding Ascii

# 4. Compile Inno Setup Installer (`Ears_Setup.exe`) if ISCC.exe is installed
$IsccPaths = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
)
$IsccExe = $IsccPaths | Where-Object { Test-Path $_ } | Select-Object -First 1

if ($IsccExe) {
    Write-Host "[4/4] Compiling Ears_Setup.exe with Inno Setup..." -ForegroundColor Green
    & $IsccExe (Join-Path $ScriptDir "ears_setup.iss")
} else {
    Write-Host "[4/4] Portable Windows bundle ready at: $DistDir" -ForegroundColor Green
    Write-Host "      (Install Inno Setup 6 to also compile Ears_Setup.exe)" -ForegroundColor Cyan
}

# ==============================================================================
# EARS -- Windows 11 / 10 Native Setup & System Tray Installer
# Audits stock Windows 11 prerequisites (VC++ Redistributable, real Python 3
# vs. Microsoft Store stub, FFmpeg, and NVIDIA CUDA DLLs) and records any newly
# added winget packages in `.install_manifest` for clean uninstall.
# ==============================================================================
param(
    [switch]$NoShortcut,
    [switch]$EnableAutostart
)

$ErrorActionPreference = "Continue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location -LiteralPath $AppDir
$ManifestFile = Join-Path $AppDir ".install_manifest"

if (-not (Test-Path $ManifestFile)) {
    New-Item -ItemType File -Path $ManifestFile -Force | Out-Null
}

function Add-ManifestEntry([string]$Entry) {
    $current = Get-Content $ManifestFile -ErrorAction SilentlyContinue
    if ($current -notcontains $Entry) {
        Add-Content -Path $ManifestFile -Value $Entry -Encoding Ascii
    }
}

function Refresh-SessionPath {
    $MachinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    $env:Path = "$MachinePath;$UserPath"
    $WingetLinks = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Links"
    if ((Test-Path $WingetLinks) -and ($env:Path -notlike "*$WingetLinks*")) {
        $env:Path = "$WingetLinks;$env:Path"
    }
    # Also scan standard Python 3.11 & FFmpeg install locations in case WinGet installed to ProgramFiles or LocalAppData
    $ExtraDirs = @(
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311"),
        (Join-Path $env:LOCALAPPDATA "Programs\Python\Python311\Scripts"),
        (Join-Path $env:ProgramFiles "Python311"),
        (Join-Path $env:ProgramFiles "Python311\Scripts")
    )
    foreach ($d in $ExtraDirs) {
        if ((Test-Path $d) -and ($env:Path -notlike "*$d*")) {
            $env:Path = "$d;$env:Path"
        }
    }
}

function Test-RealPython {
    try {
        $cmd = Get-Command python -ErrorAction SilentlyContinue
        if (-not $cmd) { return $false }
        $out = & python -c "import sys; print(sys.version_info[0])" 2>$null
        return ($LASTEXITCODE -eq 0 -and ("$out".Trim() -eq "3"))
    } catch {
        return $false
    }
}

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  EARS -- ZERO-GAP RTSP AUDIO CAPTIONING UTILITY (WINDOWS INSTALLER)" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  Install Directory : $AppDir"
Write-Host ""

# Stop any existing Ears background process before installing/updating
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($_.Name -match "^pythonw?\.exe$") -and ($_.CommandLine -like "*ears.py*")
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}

# Clean up any leftover test or foreign-machine config/data copied with the repo
$CfgFile = Join-Path $AppDir "config.json"
$ExpectedFp = "win32:" + $env:COMPUTERNAME.ToLower()
if (Test-Path $CfgFile) {
    $RawCfg = Get-Content $CfgFile -Raw -ErrorAction SilentlyContinue
    if (($RawCfg -notlike "*$ExpectedFp*") -or ($RawCfg -like "*/home/*")) {
        Write-Host "  -> Cleaning leftover test/foreign-machine configuration and test data..." -ForegroundColor Yellow
        Remove-Item -Path $CfgFile -Force -ErrorAction SilentlyContinue
        Remove-Item -Path (Join-Path $AppDir "data\ears.db"), (Join-Path $AppDir "data\logs"), (Join-Path $AppDir "data\audio"), (Join-Path $AppDir "data\phase2_test_output") -Recurse -Force -ErrorAction SilentlyContinue
        if (Test-Path "C:\home\fuzz\Projects\ears") {
            Remove-Item -Path "C:\home\fuzz" -Recurse -Force -ErrorAction SilentlyContinue
        }
    }
}
Remove-Item -Path (Join-Path $AppDir "data\phase2_test_output") -Recurse -Force -ErrorAction SilentlyContinue

# 1. Verify VC++ Redistributable, Python 3, and FFmpeg (Record in manifest ONLY if we install them!)
Write-Host "[1/4] Auditing Windows 11 system prerequisites (VC++ Runtime, Python 3, FFmpeg)..." -ForegroundColor Green

$VcDll = Join-Path $env:SystemRoot "System32\vcruntime140_1.dll"
if (Test-Path $VcDll) {
    Write-Host "  -> [Already Installed] Microsoft VC++ 2015-2022 Redistributable (will NOT be removed on uninstall)"
} else {
    Write-Host "  -> Installing Microsoft VC++ 2015-2022 Redistributable (required by faster-whisper/onnxruntime)..." -ForegroundColor Yellow
    winget install -e --id Microsoft.VCRedist.2015+.x64 --accept-package-agreements --accept-source-agreements
    Add-ManifestEntry "WINGET_PKG:Microsoft.VCRedist.2015+.x64"
    Refresh-SessionPath
}

Refresh-SessionPath
if (Test-RealPython) {
    Write-Host "  -> [Already Installed] Python 3 (will NOT be removed on uninstall)"
} else {
    Write-Host "  -> Real Python 3 not found (only Windows Store stub present). Installing Python 3.11 via winget..." -ForegroundColor Yellow
    winget install -e --id Python.Python.3.11 --accept-package-agreements --accept-source-agreements
    Add-ManifestEntry "WINGET_PKG:Python.Python.3.11"
    Refresh-SessionPath
}

if (Get-Command ffmpeg -ErrorAction SilentlyContinue) {
    Write-Host "  -> [Already Installed] FFmpeg (will NOT be removed on uninstall)"
} else {
    Write-Host "  -> FFmpeg not found in PATH. Installing Gyan.FFmpeg via winget..." -ForegroundColor Yellow
    winget install -e --id Gyan.FFmpeg --accept-package-agreements --accept-source-agreements
    Add-ManifestEntry "WINGET_PKG:Gyan.FFmpeg"
    Refresh-SessionPath
}

# 2. Create Isolated Virtual Environment & Install Dependencies
$VenvDir = Join-Path $AppDir "venv"
$VenvPy = Join-Path $VenvDir "Scripts\python.exe"
$VenvPyw = Join-Path $VenvDir "Scripts\pythonw.exe"

Write-Host "[2/4] Setting up isolated Python virtual environment ($VenvDir)..." -ForegroundColor Green
Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    ($_.Name -match "^pythonw?\.exe$") -and ($_.CommandLine -like "*ears.py*")
} | ForEach-Object {
    cmd.exe /c "taskkill /F /T /PID $($_.ProcessId) >nul 2>&1"
}

if (-not (Test-Path $VenvPy)) {
    python -m venv "$VenvDir"
    Add-ManifestEntry "VENV:$VenvDir"
}

Write-Host "  -> Installing Ears Python dependencies (faster-whisper, fastapi, pystray)..." -ForegroundColor Cyan
& $VenvPy -m pip install --disable-pip-version-check --upgrade pip wheel
& $VenvPy -m pip install --disable-pip-version-check -r (Join-Path $AppDir "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: pip install failed with exit code $LASTEXITCODE" -ForegroundColor Red
    exit $LASTEXITCODE
}

$HasNvidia = $false
try {
    if (Get-Command nvidia-smi -ErrorAction SilentlyContinue) {
        $HasNvidia = $true
    }
} catch {}

if ($HasNvidia) {
    Write-Host "  -> NVIDIA GPU detected! Installing CUDA 12 cuBLAS/cuDNN runtime inside venv..." -ForegroundColor Cyan
    & $VenvPy -m pip install --disable-pip-version-check nvidia-cublas-cu12 nvidia-cudnn-cu12
}

# 3. Create Windowless Tray Launcher (.vbs) & Console Launcher (.bat)
Write-Host "[3/4] Creating System Tray launchers and Desktop / Start Menu shortcuts..." -ForegroundColor Green
$TrayVbs = Join-Path $AppDir "Start_Ears_Tray.vbs"
$EarsPy = Join-Path $AppDir "ears.py"

$VbsLines = @(
    'Set WshShell = CreateObject("WScript.Shell")',
    ('WshShell.CurrentDirectory = "' + $AppDir + '"'),
    ('WshShell.Run """' + $VenvPyw + '"" ""' + $EarsPy + '"" --tray --open-browser", 0, False')
)
Set-Content -Path $TrayVbs -Value $VbsLines -Encoding Ascii
Add-ManifestEntry "FILE:$TrayVbs"

$ConsoleBat = Join-Path $AppDir "Start_Ears_Console.bat"
$BatLines = @(
    '@echo off',
    ('cd /d "' + $AppDir + '"'),
    ('"' + $VenvPy + '" "' + $EarsPy + '" --tray --open-browser'),
    'pause'
)
Set-Content -Path $ConsoleBat -Value $BatLines -Encoding Ascii
Add-ManifestEntry "FILE:$ConsoleBat"

$WshShell = New-Object -ComObject WScript.Shell
$DesktopPath = [Environment]::GetFolderPath("Desktop")
$StartMenuPath = [Environment]::GetFolderPath("Programs")

$ShortcutDirs = @($StartMenuPath)
if (-not $NoShortcut) {
    $ShortcutDirs += $DesktopPath
}

foreach ($Dir in $ShortcutDirs) {
    $ShortcutPath = Join-Path $Dir "Ears Audio Captioner.lnk"
    $Shortcut = $WshShell.CreateShortcut($ShortcutPath)
    $Shortcut.TargetPath = "wscript.exe"
    $Shortcut.Arguments = "`"$TrayVbs`""
    $Shortcut.WorkingDirectory = $AppDir
    $Shortcut.Description = "Ears -- Zero-Gap RTSP Audio Captioning Utility"
    $Shortcut.Save()
    Add-ManifestEntry "SHORTCUT:$ShortcutPath"
}

if ($EnableAutostart) {
    $RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
    $RunCmd = "`"$VenvPyw`" `"$EarsPy`" --tray"
    Set-ItemProperty -Path $RunKey -Name "EarsAudioCaptioner" -Value $RunCmd -ErrorAction SilentlyContinue
}

# 4. Hardware Probe Summary & Mandatory Launch for Initial Setup
Write-Host "[4/4] Verifying hardware profile..." -ForegroundColor Green
& $VenvPy -c "import sys; sys.path.insert(0, r'$AppDir'); from backend.hardware import probe_and_select_hardware; print('  -> Selected Profile:', probe_and_select_hardware()['banner_badge'])"

Write-Host ""
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  EARS WINDOWS INSTALLATION COMPLETE!" -ForegroundColor Green
Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  Desktop Shortcut : Ears Audio Captioner"
Write-Host "  Install Manifest : $ManifestFile"
Write-Host "  Web Dashboard    : http://127.0.0.1:8765"
Write-Host "  Clean Uninstall  : Double-click Uninstall_Ears_GUI_Windows.bat"
Write-Host "==============================================================================" -ForegroundColor Cyan

Write-Host "Launching Ears in the Windows System Tray and opening Web Setup Wizard..." -ForegroundColor Cyan
Start-Process "wscript.exe" -ArgumentList "`"$TrayVbs`""

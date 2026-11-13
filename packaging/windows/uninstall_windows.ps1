# ==============================================================================
# EARS -- Clean, Manifest-Aware Windows Uninstaller
# Reads `.install_manifest` so it ONLY offers to remove winget packages that Ears
# itself installed, leaving pre-existing Python/FFmpeg installations untouched.
# ==============================================================================
param(
    [switch]$Purge,
    [switch]$NonInteractive,
    [switch]$RemoveWinget,
    [switch]$DeleteData
)

$ErrorActionPreference = "SilentlyContinue"
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$AppDir = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
Set-Location -LiteralPath $AppDir
$ManifestFile = Join-Path $AppDir ".install_manifest"

Write-Host "==============================================================================" -ForegroundColor Cyan
Write-Host "  EARS -- MANIFEST-AWARE WINDOWS UNINSTALLER" -ForegroundColor Cyan
Write-Host "==============================================================================" -ForegroundColor Cyan

# 1. Stop running Ears python/pythonw processes AND child ffmpeg stream workers
Write-Host "[1/5] Stopping running Ears processes..." -ForegroundColor Green
Get-CimInstance Win32_Process | Where-Object {
    (($_.Name -match "^pythonw?\.exe$") -and ($_.CommandLine -like "*ears.py*")) -or
    (($_.Name -match "^ffmpeg\.exe$") -and ($_.CommandLine -like "*ears_buffers*"))
} | ForEach-Object {
    cmd.exe /c "taskkill /F /T /PID $($_.ProcessId) >nul 2>&1"
    Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
}
Start-Sleep -Seconds 1

# 2. Remove Registry Start-at-Login entry & Shortcuts
Write-Host "[2/5] Removing Start-at-Login Registry key and Desktop/Start Menu shortcuts..." -ForegroundColor Green
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "EarsAudioCaptioner" -ErrorAction SilentlyContinue

$DesktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "Ears Audio Captioner.lnk"
$StartMenuShortcut = Join-Path ([Environment]::GetFolderPath("Programs")) "Ears Audio Captioner.lnk"
Remove-Item -Path $DesktopShortcut, $StartMenuShortcut -Force -ErrorAction SilentlyContinue

# 3. Remove Temporary Buffers & Launchers
Write-Host "[3/5] Cleaning temporary audio buffers and generated launchers..." -ForegroundColor Green
$TempBuf = Join-Path ([System.IO.Path]::GetTempPath()) "ears_buffers"
Remove-Item -Path $TempBuf -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item -Path (Join-Path $AppDir "Start_Ears_Tray.vbs"), (Join-Path $AppDir "Start_Ears_Console.bat") -Force -ErrorAction SilentlyContinue

# 4. Remove Virtual Environment, Downloaded Model Cache & Check `.install_manifest` for Winget Packages
Write-Host "[4/5] Removing Python virtual environment, model cache and checking `.install_manifest`..." -ForegroundColor Green
Remove-Item -Path (Join-Path $AppDir "venv"), (Join-Path $AppDir ".venv"), (Join-Path $AppDir "data\models") -Recurse -Force -ErrorAction SilentlyContinue

if (Test-Path $ManifestFile) {
    $Lines = Get-Content $ManifestFile -ErrorAction SilentlyContinue
    $WingetAdded = @()
    foreach ($Line in $Lines) {
        if ($Line -like "WINGET_PKG:*") {
            $WingetAdded += $Line.Substring(11)
        }
    }
    if ($WingetAdded.Count -gt 0) {
        Write-Host "  -> Manifest shows Ears installed these winget packages: $($WingetAdded -join ', ')" -ForegroundColor Cyan
        Write-Host "     (Pre-existing packages were not recorded and will never be touched.)" -ForegroundColor Cyan
        $RemoveSys = $false
        if ($Purge -or $RemoveWinget) {
            $RemoveSys = $true
        } elseif (-not $NonInteractive) {
            $ansSys = Read-Host "Uninstall the winget packages that Ears added ($($WingetAdded -join ', '))? [y/N]"
            if ($ansSys -match "^[Yy]") {
                $RemoveSys = $true
            }
        }
        if ($RemoveSys) {
            foreach ($PkgId in $WingetAdded) {
                if ($PkgId -like "*FFmpeg*") {
                    Get-Process -Name "ffmpeg","ffprobe" -ErrorAction SilentlyContinue | Stop-Process -Force -ErrorAction SilentlyContinue
                    Start-Sleep -Seconds 1
                }
                winget uninstall -e --id $PkgId --accept-source-agreements
            }
        }
    } else {
        Write-Host "  -> Python and FFmpeg were already installed before Ears; leaving them intact."
    }
    Remove-Item -Path $ManifestFile -Force -ErrorAction SilentlyContinue
} else {
    Write-Host "  -> No `.install_manifest` found; leaving system Python and FFmpeg intact."
}

# 5. Optionally Purge Data
Write-Host "[5/5] Checking user transcripts and configuration..." -ForegroundColor Green
if ($Purge -or $DeleteData) {
    Write-Host "  -> Removing data\ and config.json..." -ForegroundColor Yellow
    Remove-Item -Path (Join-Path $AppDir "data"), (Join-Path $AppDir "config.json") -Recurse -Force -ErrorAction SilentlyContinue
} elseif ($NonInteractive) {
    Write-Host "  -> Preserved your transcripts and config.json in $AppDir." -ForegroundColor Cyan
} else {
    $ans = Read-Host "Do you also want to delete saved transcripts, audio clips, and config.json? [y/N]"
    if ($ans -match "^[Yy]") {
        Remove-Item -Path (Join-Path $AppDir "data"), (Join-Path $AppDir "config.json") -Recurse -Force -ErrorAction SilentlyContinue
        Write-Host "  -> Deleted data\ and config.json." -ForegroundColor Yellow
    } else {
        Write-Host "  -> Preserved your transcripts and config.json in $AppDir." -ForegroundColor Cyan
    }
}

Write-Host ""
Write-Host "Ears has been cleanly uninstalled from Windows." -ForegroundColor Green

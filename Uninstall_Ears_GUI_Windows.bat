@echo off
REM ============================================================================
REM EARS -- Windows Graphical Uninstaller (Double-Click in File Explorer)
REM Launches the Dark Obsidian & Royal Purple GUI Uninstaller.
REM (Pass --cli to run in terminal mode instead.)
REM ============================================================================
cd /d "%~dp0"
if /I "%~1"=="--cli" (
    powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0packaging\windows\uninstall_windows.ps1" %*
    pause
) else (
    start "" powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0packaging\windows\gui_installer.ps1" -Mode uninstall
)

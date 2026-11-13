#!/usr/bin/env bash
# ==============================================================================
# EARS — macOS Native Cocoa GUI Installer & Uninstaller Launcher
# 1. Launches the native Cocoa AppKit GUI Wizard (`gui_wizard_macos.js` via JXA)
#    with a live determinate progress bar, step status, and live scrolling log.
# 2. Falls back to standard AppleScript dialogs if needed, and always brings
#    the Web Setup Wizard (`http://127.0.0.1:8765`) to the foreground.
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODE="${1:-install}"

xattr -cr "$APP_DIR" 2>/dev/null || true

# Tier 1: Native Cocoa AppKit GUI Window with Live Progress Bar & Scrolling Log
if command -v osascript >/dev/null 2>&1 && [ -f "${SCRIPT_DIR}/gui_wizard_macos.js" ]; then
    set +e
    osascript -l JavaScript "${SCRIPT_DIR}/gui_wizard_macos.js" "$MODE" "$APP_DIR" 2>/dev/null
    JXA_RC=$?
    set -e
    if [ "$JXA_RC" -eq 0 ]; then
        exit 0
    fi
fi

# Tier 2: Standard AppleScript Dialog Fallback
if [ "$MODE" = "install" ]; then
    CHOICE="$(osascript -e 'display dialog "Welcome to Ears Audio Captioner!\n\nThis wizard will set up Ears in your Applications folder, configure the AI speech-to-text engine (100% local), and start the macOS Menu Bar icon.\n\nInstall Folder:\n'"${APP_DIR}"'" with title "Ears Audio Captioner — macOS Setup" buttons {"Cancel", "Install Ears"} default button "Install Ears" with icon note' -e 'button returned of result' 2>/dev/null || echo "Cancel")"

    if [ "$CHOICE" != "Install Ears" ]; then
        exit 0
    fi

    LOG_FILE="/tmp/ears_macos_install.log"
    if bash "${SCRIPT_DIR}/install_macos.sh" > "$LOG_FILE" 2>&1; then
        open "http://127.0.0.1:8765" 2>/dev/null || true
        osascript -e 'display dialog "Ears Audio Captioner is now installed and running in your macOS Menu Bar!\n\n• App Bundle: /Applications/Ears.app\n• Uninstaller: /Applications/Uninstall Ears.app\n• Web UI: http://127.0.0.1:8765\n\nClick Continue to Setup to open the Web Setup Wizard." with title "Ears Installation Complete" buttons {"Continue to Setup"} default button "Continue to Setup" with icon note' >/dev/null 2>&1 || true
        open "http://127.0.0.1:8765" 2>/dev/null || true
    else
        TAIL_ERR="$(tail -n 12 "$LOG_FILE" 2>/dev/null | tr '"' "'" || echo "See $LOG_FILE")"
        osascript -e "display alert \"Ears Installation Encountered an Issue\" message \"${TAIL_ERR}\" as critical" 2>/dev/null || true
        exit 1
    fi
else
    U_CHOICE="$(osascript -e 'display dialog "How would you like to uninstall Ears Audio Captioner?\n\nPre-existing system packages (Python/Homebrew) will never be touched." with title "Uninstall Ears Audio Captioner" buttons {"Cancel", "Delete Everything", "Keep My Transcripts"} default button "Keep My Transcripts" with icon caution' -e 'button returned of result' 2>/dev/null || echo "Cancel")"

    if [ "$U_CHOICE" = "Cancel" ]; then
        exit 0
    elif [ "$U_CHOICE" = "Delete Everything" ]; then
        bash "${SCRIPT_DIR}/uninstall_macos.sh" --purge > /tmp/ears_macos_uninstall.log 2>&1
        osascript -e 'display dialog "Ears Audio Captioner and all saved transcripts/settings have been cleanly removed from your Mac." with title "Ears Uninstalled" buttons {"OK"} default button "OK" with icon note' 2>/dev/null || true
    elif [ "$U_CHOICE" = "Keep My Transcripts" ]; then
        bash "${SCRIPT_DIR}/uninstall_macos.sh" --keep-data > /tmp/ears_macos_uninstall.log 2>&1
        osascript -e 'display dialog "Ears Audio Captioner has been cleanly uninstalled.\n\nYour saved transcripts and config.json were preserved in:\n'"${APP_DIR}"'" with title "Ears Uninstalled" buttons {"OK"} default button "OK" with icon note' 2>/dev/null || true
    fi
fi

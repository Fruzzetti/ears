#!/usr/bin/env bash
# ==============================================================================
# EARS — Multi-Tier Linux GUI Setup & Uninstall Launcher
# Tries every available graphical toolkit in priority order before ever falling
# back to a command-line terminal:
#   1. PyQt5 / PyQt6 Native Window (Ubuntu LXQt / Lubuntu & KDE Plasma)
#   2. GTK3 (`python3-gi`) Native Window (Ubuntu GNOME, Mint, Debian, Fedora)
#   3. Tkinter (`python3-tk`) Native Window
#   4. Zero-Dependency Local Browser Setup Wizard (`http.server` + `xdg-open`)
#   5. Native Desktop Dialog Utilities (`qarma` -> `kdialog` -> `zenity`)
#   6. Terminal Emulator Fallback (`qterminal` / `x-terminal-emulator` / CLI)
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MODE="${1:-install}"

# Helper to run a privileged command via pkexec, lxqt-sudo, or sudo
run_privileged() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif sudo -n true 2>/dev/null; then
        sudo -n env DISPLAY="${DISPLAY:-}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" XAUTHORITY="${XAUTHORITY:-}" SUDO_USER="$USER" "$@"
    elif command -v pkexec >/dev/null 2>&1; then
        pkexec env DISPLAY="${DISPLAY:-}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" XAUTHORITY="${XAUTHORITY:-}" SUDO_USER="$USER" "$@"
    elif command -v lxqt-sudo >/dev/null 2>&1; then
        lxqt-sudo -s env DISPLAY="${DISPLAY:-}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" XAUTHORITY="${XAUTHORITY:-}" SUDO_USER="$USER" "$@"
    else
        sudo env DISPLAY="${DISPLAY:-}" WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-}" XAUTHORITY="${XAUTHORITY:-}" SUDO_USER="$USER" "$@"
    fi
}

# Tiers 1 - 4: Python Multi-Engine GUI Wizard (PyQt5/PyQt6 -> GTK3 -> Tkinter -> Local Web Wizard)
if { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; } && command -v python3 >/dev/null 2>&1; then
    set +e
    python3 "${SCRIPT_DIR}/gui_wizard_linux.py" "$MODE"
    WIZ_RC=$?
    set -e
    if [ "$WIZ_RC" -eq 0 ]; then
        exit 0
    fi
fi

# Tier 5: Native Desktop Dialogs (qarma / zenity / kdialog)
DIALOG_BIN=""
if { [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; }; then
    if command -v qarma >/dev/null 2>&1; then
        DIALOG_BIN="qarma"
    elif command -v zenity >/dev/null 2>&1; then
        DIALOG_BIN="zenity"
    elif command -v kdialog >/dev/null 2>&1; then
        DIALOG_BIN="kdialog"
    fi
fi

if [ -n "$DIALOG_BIN" ]; then
    if [ "$DIALOG_BIN" = "kdialog" ]; then
        if [ "$MODE" = "install" ]; then
            kdialog --title "Ears Audio Captioner — Linux Setup" --yesno "Welcome to Ears Audio Captioner!\n\nInstall Folder: ${APP_DIR}\n\nProceed with installation?" 2>/dev/null || exit 0
            LOG_FILE="/tmp/ears_linux_install.log"
            rm -f "$LOG_FILE"
            run_privileged bash "${APP_DIR}/install.sh" --cli > "$LOG_FILE" 2>&1 || true
            if grep -q "EARS INSTALLATION COMPLETE" "$LOG_FILE" 2>/dev/null; then
                nohup "${APP_DIR}/venv/bin/python3" "${APP_DIR}/ears.py" --tray --open-browser >/dev/null 2>&1 &
                kdialog --title "Ears Installation Complete" --msgbox "Ears Audio Captioner is now installed and running!\n\nWeb UI: http://127.0.0.1:8765 (opened in your browser)" 2>/dev/null || true
                exit 0
            else
                ERR_MSG="$(tail -n 12 "$LOG_FILE" 2>/dev/null || echo "See $LOG_FILE")"
                kdialog --title "Ears Installation Error" --error "Installation encountered an issue:\n\n${ERR_MSG}" 2>/dev/null || true
                exit 1
            fi
        else
            kdialog --title "Uninstall Ears Audio Captioner" --yesno "Uninstall Ears Audio Captioner?\n\nPre-existing system packages will never be touched." 2>/dev/null || exit 0
            U_FLAGS="--keep-data"
            if kdialog --title "Saved Transcripts & Settings" --yesno "Do you also want to delete your saved transcripts, audio clips, and config.json?" 2>/dev/null; then
                U_FLAGS="--purge"
            fi
            run_privileged bash "${APP_DIR}/uninstall.sh" --cli "$U_FLAGS" > /tmp/ears_linux_uninstall.log 2>&1 || true
            kdialog --title "Ears Uninstalled" --msgbox "Ears Audio Captioner has been cleanly uninstalled." 2>/dev/null || true
            exit 0
        fi
    else
        # zenity or qarma (compatible CLI flags)
        if [ "$MODE" = "install" ]; then
            "$DIALOG_BIN" --question \
                --title="Ears Audio Captioner — Linux Setup" \
                --width=460 \
                --ok-label="Install Ears" \
                --cancel-label="Cancel" \
                --text="<b>Welcome to Ears Audio Captioner!</b>\n\nThis wizard will:\n• Audit missing system packages (recorded in .install_manifest)\n• Set up an isolated Python environment &amp; Whisper engine\n• Register the background service &amp; System Tray icon\n\nInstall Folder:\n<tt>${APP_DIR}</tt>" \
                2>/dev/null || exit 0

            LOG_FILE="/tmp/ears_linux_install.log"
            rm -f "$LOG_FILE"

            (
                echo "15"
                echo "# [1/4] Auditing system packages & dependencies..."
                run_privileged bash "${APP_DIR}/install.sh" --cli > "$LOG_FILE" 2>&1
                echo "100"
                echo "# Installation complete!"
            ) | "$DIALOG_BIN" --progress \
                --title="Installing Ears Audio Captioner" \
                --text="Starting installation..." \
                --pulsate \
                --auto-close \
                --width=460 2>/dev/null || true

            if grep -q "EARS INSTALLATION COMPLETE" "$LOG_FILE" 2>/dev/null; then
                if ! pgrep -f "ears.py --tray" >/dev/null 2>&1; then
                    nohup "${APP_DIR}/venv/bin/python3" "${APP_DIR}/ears.py" --tray --open-browser >/dev/null 2>&1 &
                else
                    xdg-open "http://127.0.0.1:8765" >/dev/null 2>&1 &
                fi
                "$DIALOG_BIN" --info \
                    --title="Ears Installation Complete" \
                    --width=440 \
                    --ok-label="Continue to Setup" \
                    --text="<b>Ears Audio Captioner is now installed and running!</b>\n\n• Web UI: <tt>http://127.0.0.1:8765</tt> (opened in your browser)\n• System Tray icon launched in panel" 2>/dev/null || true
                exit 0
            else
                ERR_MSG="$(tail -n 12 "$LOG_FILE" 2>/dev/null || echo "See $LOG_FILE")"
                "$DIALOG_BIN" --error --title="Ears Installation Error" --width=480 --text="Installation encountered an issue:\n\n<tt>${ERR_MSG}</tt>" 2>/dev/null || true
                exit 1
            fi
        else
            "$DIALOG_BIN" --question \
                --title="Uninstall Ears Audio Captioner" \
                --width=440 \
                --ok-label="Continue to Uninstall" \
                --cancel-label="Cancel" \
                --text="<b>Uninstall Ears Audio Captioner?</b>\n\nPre-existing system packages (Python, FFmpeg, etc.) will never be touched." 2>/dev/null || exit 0

            U_FLAGS="--keep-data"
            if "$DIALOG_BIN" --question \
                --title="Saved Transcripts & Settings" \
                --width=440 \
                --ok-label="Delete Transcripts Too" \
                --cancel-label="Keep My Transcripts" \
                --text="Do you also want to delete your saved transcripts, audio clips, and <tt>config.json</tt>?" 2>/dev/null; then
                U_FLAGS="--purge"
            fi

            (
                echo "20"
                echo "# Removing Ears service, virtualenv, and manifest packages..."
                run_privileged bash "${APP_DIR}/uninstall.sh" --cli "$U_FLAGS" > /tmp/ears_linux_uninstall.log 2>&1
                echo "100"
            ) | "$DIALOG_BIN" --progress \
                --title="Uninstalling Ears" \
                --text="Cleaning up..." \
                --pulsate \
                --auto-close \
                --width=420 2>/dev/null || true

            "$DIALOG_BIN" --info --title="Ears Uninstalled" --width=400 --text="Ears Audio Captioner has been cleanly uninstalled." 2>/dev/null || true
            exit 0
        fi
    fi
fi

# Tier 6: Terminal Emulator / CLI Fallback
TARGET_CMD="${APP_DIR}/install.sh"
if [ "$MODE" != "install" ]; then
    TARGET_CMD="${APP_DIR}/uninstall.sh"
fi

if [ -t 0 ] && [ -t 1 ]; then
    exec bash "$TARGET_CMD" --cli
fi

for TERM_BIN in qterminal lxterminal konsole gnome-terminal xfce4-terminal mate-terminal x-terminal-emulator xterm; do
    if command -v "$TERM_BIN" >/dev/null 2>&1; then
        exec "$TERM_BIN" -e bash -c "bash \"$TARGET_CMD\" --cli; echo ''; read -r -p 'Press Enter to close...' _"
    fi
done

exec bash "$TARGET_CMD" --cli

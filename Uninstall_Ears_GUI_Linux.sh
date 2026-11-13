#!/usr/bin/env bash
# ==============================================================================
# EARS — Linux Graphical Uninstaller (Double-Click in File Manager)
# Tries PyQt5/PyQt6 -> GTK3 -> Tkinter -> Local Browser Wizard ->
# qarma/zenity/kdialog before ever falling back to a command-line terminal.
# ==============================================================================
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(uname -s)" = "Darwin" ]; then
    xattr -cr "$APP_DIR" 2>/dev/null || true
    exec bash "${APP_DIR}/packaging/macos/gui_macos.sh" uninstall
else
    exec bash "${APP_DIR}/packaging/linux/gui_linux.sh" uninstall
fi

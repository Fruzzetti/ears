#!/usr/bin/env bash
# ==============================================================================
# EARS — Linux Graphical Installer (Double-Click in File Manager)
# Tries PyQt5/PyQt6 -> GTK3 -> Tkinter -> Local Browser Setup Wizard ->
# qarma/zenity/kdialog before ever falling back to a command-line terminal.
# ==============================================================================
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [ "$(uname -s)" = "Darwin" ]; then
    xattr -cr "$APP_DIR" 2>/dev/null || true
    exec bash "${APP_DIR}/packaging/macos/gui_macos.sh" install
else
    exec bash "${APP_DIR}/packaging/linux/gui_linux.sh" install
fi

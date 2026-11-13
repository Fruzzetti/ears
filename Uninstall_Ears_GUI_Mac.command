#!/usr/bin/env bash
# ==============================================================================
# EARS — macOS Graphical Uninstaller (Double-Click in Finder)
# Launches the macOS Cocoa GUI Uninstaller.
# ==============================================================================
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
xattr -cr "$APP_DIR" 2>/dev/null || true
exec bash "${APP_DIR}/packaging/macos/gui_macos.sh" uninstall

#!/usr/bin/env bash
# ==============================================================================
# EARS — macOS Graphical Installer (Double-Click in Finder)
# Clears Gatekeeper quarantine attributes and launches the macOS Cocoa GUI Wizard.
# ==============================================================================
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
xattr -cr "$APP_DIR" 2>/dev/null || true
exec bash "${APP_DIR}/packaging/macos/gui_macos.sh" install

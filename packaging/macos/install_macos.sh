#!/usr/bin/env bash
# ==============================================================================
# EARS — macOS Installer & Menu Bar App Bundle Builder (Apple Silicon & Intel)
# Tracks any newly added Homebrew packages (`ffmpeg`) in `.install_manifest`
# so `uninstall_macos.sh` only removes what Ears itself installed.
# ==============================================================================
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MANIFEST_FILE="${APP_DIR}/.install_manifest"
ARCH="$(uname -m)"

# Clear macOS Gatekeeper quarantine attributes if extracted from a downloaded archive
xattr -cr "${APP_DIR}" 2>/dev/null || true

touch "$MANIFEST_FILE"

rm -rf "${APP_DIR}/data/phase2_test_output" 2>/dev/null || true
if [ -f "${APP_DIR}/config.json" ]; then
    HOST_LC="$(hostname | tr '[:upper:]' '[:lower:]')"
    if ! grep -qi "darwin:${HOST_LC}" "${APP_DIR}/config.json" 2>/dev/null; then
        echo -e "${YELLOW}  -> Cleaning leftover test/foreign-machine configuration...${NC}"
        rm -rf "${APP_DIR}/config.json" "${APP_DIR}/data/ears.db" "${APP_DIR}/data/logs" "${APP_DIR}/data/audio" 2>/dev/null || true
    fi
fi

record_manifest() {
    local entry="$1"
    if ! grep -qxF "$entry" "$MANIFEST_FILE" 2>/dev/null; then
        echo "$entry" >> "$MANIFEST_FILE"
    fi
}

echo -e "${CYAN}==============================================================================${NC}"
echo -e "${CYAN}  EARS — ZERO-GAP RTSP AUDIO CAPTIONING UTILITY (MACOS INSTALLER)${NC}"
echo -e "${CYAN}==============================================================================${NC}"
echo -e "  Architecture      : ${ARCH}"
echo -e "  Install Directory : ${APP_DIR}"
echo ""

# 1. Check Homebrew & FFmpeg / Python 3
echo -e "${GREEN}[1/4] Auditing Python 3 and FFmpeg...${NC}"
if python3 -c "import sys, venv" >/dev/null 2>&1; then
    echo -e "  -> [Already Installed] python3 (will NOT be removed on uninstall)"
else
    if command -v brew >/dev/null 2>&1; then
        echo -e "${YELLOW}  -> Installing python@3.11 via Homebrew...${NC}"
        brew install python@3.11
        record_manifest "BREW_PKG:python@3.11"
    fi
fi

NEED_BUNDLED_FFMPEG=0
if command -v ffmpeg >/dev/null 2>&1; then
    echo -e "  -> [Already Installed] ffmpeg (will NOT be removed on uninstall)"
else
    if command -v brew >/dev/null 2>&1; then
        echo -e "${YELLOW}  -> Installing ffmpeg via Homebrew...${NC}"
        brew install ffmpeg
        record_manifest "BREW_PKG:ffmpeg"
    else
        echo -e "${YELLOW}  -> Homebrew not installed; will bundle standalone ffmpeg inside ./venv (zero system changes).${NC}"
        NEED_BUNDLED_FFMPEG=1
    fi
fi

# 2. Create Virtualenv & Install Dependencies
echo -e "${GREEN}[2/4] Setting up isolated Python virtual environment (${APP_DIR}/venv)...${NC}"
pkill -f "ears.py" 2>/dev/null || true
if [ ! -d "${APP_DIR}/venv" ]; then
    python3 -m venv "${APP_DIR}/venv"
    record_manifest "VENV:${APP_DIR}/venv"
fi

VENV_PIP="${APP_DIR}/venv/bin/pip"
VENV_PY="${APP_DIR}/venv/bin/python3"

"$VENV_PIP" install --upgrade pip wheel >/dev/null 2>&1 || true
"$VENV_PIP" install -r "${APP_DIR}/requirements.txt"

if [ "$NEED_BUNDLED_FFMPEG" -eq 1 ] || ! command -v ffmpeg >/dev/null 2>&1; then
    echo -e "${CYAN}  -> Installing self-contained static ffmpeg binary into ./venv (imageio-ffmpeg)...${NC}"
    "$VENV_PIP" install imageio-ffmpeg >/dev/null 2>&1 || "$VENV_PIP" install imageio-ffmpeg
    FFMPEG_BIN="$("$VENV_PY" -c "import imageio_ffmpeg; print(imageio_ffmpeg.get_ffmpeg_exe())" 2>/dev/null || true)"
    if [ -n "$FFMPEG_BIN" ] && [ -f "$FFMPEG_BIN" ]; then
        chmod +x "$FFMPEG_BIN" 2>/dev/null || true
        xattr -cr "$FFMPEG_BIN" 2>/dev/null || true
        ln -sf "$FFMPEG_BIN" "${APP_DIR}/venv/bin/ffmpeg"
        echo -e "${GREEN}  -> Standalone ffmpeg ready at ${APP_DIR}/venv/bin/ffmpeg!${NC}"
    fi
fi

# 3. Create /Applications/Ears.app Bundle (macOS Menu Bar App)
echo -e "${GREEN}[3/4] Building macOS Application Bundle (/Applications/Ears.app)...${NC}"
MAC_APP="/Applications/Ears.app"
if [ ! -w "/Applications" ]; then
    MAC_APP="${HOME}/Applications/Ears.app"
fi
mkdir -p "${MAC_APP}/Contents/MacOS" "${MAC_APP}/Contents/Resources"
record_manifest "APP_BUNDLE:${MAC_APP}"

cat > "${MAC_APP}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>EarsLauncher</string>
    <key>CFBundleIdentifier</key>
    <string>com.ears.captioner</string>
    <key>CFBundleName</key>
    <string>Ears</string>
    <key>CFBundleDisplayName</key>
    <string>Ears Audio Captioner</string>
    <key>CFBundleVersion</key>
    <string>0.1.0</string>
    <key>CFBundleShortVersionString</key>
    <string>0.1.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
EOF

cat > "${MAC_APP}/Contents/MacOS/EarsLauncher" <<EOF
#!/usr/bin/env bash
export PATH="/opt/homebrew/bin:/usr/local/bin:\$PATH"
cd "${APP_DIR}"
exec "${VENV_PY}" "${APP_DIR}/ears.py" --tray --open-browser
EOF
chmod +x "${MAC_APP}/Contents/MacOS/EarsLauncher"
xattr -cr "${MAC_APP}" 2>/dev/null || true
codesign --force --deep --sign - "${MAC_APP}" 2>/dev/null || true

# Also create /Applications/Uninstall Ears.app for 1-click GUI uninstall
MAC_UNINSTALL_APP="$(dirname "${MAC_APP}")/Uninstall Ears.app"
mkdir -p "${MAC_UNINSTALL_APP}/Contents/MacOS"
record_manifest "APP_BUNDLE:${MAC_UNINSTALL_APP}"
cat > "${MAC_UNINSTALL_APP}/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleExecutable</key>
    <string>EarsUninstaller</string>
    <key>CFBundleIdentifier</key>
    <string>com.ears.uninstaller</string>
    <key>CFBundleName</key>
    <string>Uninstall Ears</string>
    <key>CFBundleDisplayName</key>
    <string>Uninstall Ears</string>
    <key>CFBundleVersion</key>
    <string>0.1.0</string>
    <key>LSUIElement</key>
    <true/>
</dict>
</plist>
EOF
cat > "${MAC_UNINSTALL_APP}/Contents/MacOS/EarsUninstaller" <<EOF
#!/usr/bin/env bash
exec bash "${SCRIPT_DIR}/gui_macos.sh" uninstall
EOF
chmod +x "${MAC_UNINSTALL_APP}/Contents/MacOS/EarsUninstaller" "${SCRIPT_DIR}/gui_macos.sh" 2>/dev/null || true
xattr -cr "${MAC_UNINSTALL_APP}" 2>/dev/null || true
codesign --force --deep --sign - "${MAC_UNINSTALL_APP}" 2>/dev/null || true
chmod +x "${SCRIPT_DIR}/uninstall_macos.sh" 2>/dev/null || true

# 4. Verify Hardware Profile & Launch Menu Bar App
echo -e "${GREEN}[4/4] Verifying hardware profile...${NC}"
"$VENV_PY" -c "
import sys
sys.path.insert(0, '${APP_DIR}')
from backend.hardware import probe_and_select_hardware
print('  -> Selected Profile:', probe_and_select_hardware()['banner_badge'])
" || true

mkdir -p "${APP_DIR}/data"

echo ""
echo -e "${CYAN}==============================================================================${NC}"
echo -e "${GREEN}  EARS MACOS INSTALLATION COMPLETE!${NC}"
echo -e "${CYAN}==============================================================================${NC}"
echo -e "  App Bundle       : ${MAC_APP}"
echo -e "  Runtime Log      : ${APP_DIR}/data/ears_runtime.log"
echo -e "  Install Manifest : ${MANIFEST_FILE}"
echo -e "  Web Dashboard    : http://127.0.0.1:8765"
echo -e "  Clean Uninstall  : Double-click Uninstall_Ears_GUI_Mac.command (or /Applications/Uninstall Ears.app)"
echo -e "${CYAN}==============================================================================${NC}"

if [ "${1:-}" != "--no-launch" ]; then
    echo -e "${CYAN}Starting Ears Menu Bar Tray & Web Server...${NC}"
    nohup "$VENV_PY" "${APP_DIR}/ears.py" --tray --open-browser > "${APP_DIR}/data/ears_runtime.log" 2>&1 &
    EARS_PID=$!
    # Wait up to 6 seconds for port 8765 to come online
    READY=0
    for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
        if lsof -i :8765 >/dev/null 2>&1 || nc -z 127.0.0.1 8765 >/dev/null 2>&1; then
            READY=1
            break
        fi
        sleep 0.5
    done
    if [ "$READY" -eq 1 ]; then
        echo -e "${GREEN}  -> Ears is live at http://127.0.0.1:8765 (PID ${EARS_PID})!${NC}"
    else
        echo -e "${YELLOW}  -> Tray process exited or still initializing; checking runtime log...${NC}"
        tail -n 15 "${APP_DIR}/data/ears_runtime.log" 2>/dev/null || true
        echo -e "${CYAN}  -> Launching Ears server in headless fallback mode...${NC}"
        nohup "$VENV_PY" "${APP_DIR}/ears.py" --headless --open-browser >> "${APP_DIR}/data/ears_runtime.log" 2>&1 &
    fi
fi

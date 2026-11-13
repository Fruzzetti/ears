#!/usr/bin/env bash
# ==============================================================================
# EARS — Cross-Distro Linux Installer (Ubuntu/Debian, Fedora/RHEL, Arch)
# Tracks pre-existing vs. newly-added system packages in `.install_manifest`
# so `uninstall.sh` can cleanly remove ONLY what Ears itself installed.
# ==============================================================================
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Automatically delegate to macOS installer when run on Darwin (macOS)
if [ "$(uname -s)" = "Darwin" ]; then
    exec bash "${APP_DIR}/packaging/macos/install_macos.sh" "$@"
fi

NO_SHORTCUT=0
NO_AUTOSTART=0
for arg in "$@"; do
    if [ "$arg" = "--gui" ]; then
        exec bash "${APP_DIR}/packaging/linux/gui_linux.sh" install
    elif [ "$arg" = "--no-shortcut" ]; then
        NO_SHORTCUT=1
    elif [ "$arg" = "--no-autostart" ]; then
        NO_AUTOSTART=1
    fi
done

MANIFEST_FILE="${APP_DIR}/.install_manifest"
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if [ -z "$TARGET_HOME" ]; then
    TARGET_HOME="$HOME"
fi

echo -e "${CYAN}==============================================================================${NC}"
echo -e "${CYAN}  EARS — ZERO-GAP RTSP AUDIO CAPTIONING UTILITY (LINUX INSTALLER)${NC}"
echo -e "${CYAN}==============================================================================${NC}"
echo -e "  Install Directory : ${APP_DIR}"
echo -e "  Service User      : ${TARGET_USER}"
echo ""

# Initialize manifest if not present (preserve earlier manifest entries on re-run)
touch "$MANIFEST_FILE"
chown "${TARGET_USER}:${TARGET_USER}" "$MANIFEST_FILE" 2>/dev/null || true

# Clean any leftover test outputs or foreign-machine config.json
rm -rf "${APP_DIR}/data/phase2_test_output" 2>/dev/null || true
if [ -f "${APP_DIR}/config.json" ]; then
    HOST_LC="$(hostname | tr '[:upper:]' '[:lower:]')"
    if ! grep -qi "linux:${HOST_LC}" "${APP_DIR}/config.json" 2>/dev/null; then
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

# ------------------------------------------------------------------------------
# 1. System Dependencies (Only install missing packages & record them!)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[1/4] Auditing system packages (only installing what is missing)...${NC}"

if command -v apt-get >/dev/null 2>&1; then
    CANDIDATE_PKGS=(python3 python3-venv python3-pip python3-tk ffmpeg)
    if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        CANDIDATE_PKGS+=(gir1.2-ayatanaappindicator3-0.1 python3-gi python3-gi-cairo gnome-shell-extension-appindicator)
    fi
    MISSING_PKGS=()
    for pkg in "${CANDIDATE_PKGS[@]}"; do
        if dpkg -s "$pkg" >/dev/null 2>&1; then
            echo -e "  -> [Already Installed] $pkg (will NOT be removed on uninstall)"
        else
            MISSING_PKGS+=("$pkg")
        fi
    done
    if [ "${#MISSING_PKGS[@]}" -gt 0 ]; then
        echo -e "${CYAN}  -> Installing missing apt packages: ${MISSING_PKGS[*]}${NC}"
        sudo apt-get update -y || true
        for pkg in "${MISSING_PKGS[@]}"; do
            if sudo apt-get install -y "$pkg"; then
                record_manifest "APT_PKG:${pkg}"
            fi
        done
    fi

elif command -v dnf >/dev/null 2>&1; then
    CANDIDATE_PKGS=(python3 python3-pip python3-tkinter ffmpeg)
    for pkg in "${CANDIDATE_PKGS[@]}"; do
        if rpm -q "$pkg" >/dev/null 2>&1; then
            echo -e "  -> [Already Installed] $pkg (will NOT be removed on uninstall)"
        else
            echo -e "${CYAN}  -> Installing missing dnf package: $pkg${NC}"
            if sudo dnf install -y "$pkg"; then
                record_manifest "DNF_PKG:${pkg}"
            fi
        fi
    done

elif command -v pacman >/dev/null 2>&1; then
    CANDIDATE_PKGS=(python python-pip tk ffmpeg)
    for pkg in "${CANDIDATE_PKGS[@]}"; do
        if pacman -Qi "$pkg" >/dev/null 2>&1; then
            echo -e "  -> [Already Installed] $pkg (will NOT be removed on uninstall)"
        else
            echo -e "${CYAN}  -> Installing missing pacman package: $pkg${NC}"
            if sudo pacman -S --noconfirm "$pkg"; then
                record_manifest "PACMAN_PKG:${pkg}"
            fi
        fi
    done
else
    echo -e "${YELLOW}  -> Unknown package manager. Ensure python3, python3-venv, and ffmpeg are installed.${NC}"
fi

# ------------------------------------------------------------------------------
# 2. Python Virtual Environment & Whisper Dependencies (100% Isolated in ./venv)
# ------------------------------------------------------------------------------
echo -e "${GREEN}[2/4] Setting up isolated Python virtual environment (${APP_DIR}/venv)...${NC}"
if [ ! -d "${APP_DIR}/venv" ]; then
    python3 -m venv "${APP_DIR}/venv"
    record_manifest "VENV:${APP_DIR}/venv"
fi

VENV_PIP="${APP_DIR}/venv/bin/pip"
VENV_PY="${APP_DIR}/venv/bin/python3"

"$VENV_PIP" install --upgrade pip wheel >/dev/null 2>&1 || true
"$VENV_PIP" install -r "${APP_DIR}/requirements.txt" || {
    echo -e "${YELLOW}  -> Offline or restricted network detected; checking local wheel/venv cache...${NC}"
}

if command -v nvidia-smi >/dev/null 2>&1 || lspci 2>/dev/null | grep -qi "nvidia"; then
    echo -e "${CYAN}  -> NVIDIA GPU detected! Installing CUDA 12 cuBLAS/cuDNN runtime inside venv...${NC}"
    "$VENV_PIP" install nvidia-cublas-cu12 nvidia-cudnn-cu12 || true
fi

chown -R "${TARGET_USER}:${TARGET_USER}" "${APP_DIR}/venv" 2>/dev/null || true

# ------------------------------------------------------------------------------
# 3. Pre-warm Hardware Probe
# ------------------------------------------------------------------------------
echo -e "${GREEN}[3/4] Running hardware auto-detection...${NC}"
"$VENV_PY" -c "
import sys
sys.path.insert(0, '${APP_DIR}')
from backend.hardware import probe_and_select_hardware
hw = probe_and_select_hardware()
print('  -> Selected Profile:', hw['banner_badge'])
" || true

# ------------------------------------------------------------------------------
# 4. Systemd Service (`ears.service`) & Desktop Entry
# ------------------------------------------------------------------------------
echo -e "${GREEN}[4/4] Configuring systemd service (ears.service) & desktop launcher...${NC}"

NVIDIA_LD_PATHS="$(find "${APP_DIR}/venv" -type d -name "lib" 2>/dev/null | grep "nvidia" | tr '\n' ':' || true)"

SERVICE_FILE="/etc/systemd/system/ears.service"
sudo bash -c "cat > ${SERVICE_FILE}" <<EOF
[Unit]
Description=Ears — Zero-Gap RTSP Audio Captioning Utility
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${TARGET_USER}
WorkingDirectory=${APP_DIR}
Environment="PATH=${APP_DIR}/venv/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
Environment="LD_LIBRARY_PATH=${NVIDIA_LD_PATHS}"
Environment="PYTHONUNBUFFERED=1"
ExecStart=${VENV_PY} ${APP_DIR}/ears.py --headless
Restart=always
RestartSec=5
TimeoutStopSec=5

[Install]
WantedBy=multi-user.target
EOF
record_manifest "SYSTEMD:${SERVICE_FILE}"

sudo systemctl daemon-reload
sudo systemctl enable ears.service
sudo systemctl restart ears.service

DESKTOP_DIR="${TARGET_HOME}/.local/share/applications"
USER_DESKTOP_FOLDER="${TARGET_HOME}/Desktop"
AUTOSTART_DIR="${TARGET_HOME}/.config/autostart"
if [ -d "${TARGET_HOME}/.local/share" ] || [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    mkdir -p "$DESKTOP_DIR" "$AUTOSTART_DIR"
    if [ "$NO_SHORTCUT" -eq 0 ]; then
        cat > "${DESKTOP_DIR}/ears.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ears Audio Captioner
Comment=Zero-Gap RTSP Audio Captioning Utility (System Tray + Web UI)
Exec=${VENV_PY} ${APP_DIR}/ears.py --tray --open-browser
Path=${APP_DIR}
Terminal=false
Categories=AudioVideo;Utility;
EOF
        chown "${TARGET_USER}:${TARGET_USER}" "${DESKTOP_DIR}/ears.desktop" 2>/dev/null || true
        chmod +x "${DESKTOP_DIR}/ears.desktop" || true
        record_manifest "FILE:${DESKTOP_DIR}/ears.desktop"
        if [ -d "$USER_DESKTOP_FOLDER" ]; then
            cp -f "${DESKTOP_DIR}/ears.desktop" "${USER_DESKTOP_FOLDER}/Ears Audio Captioner.desktop" 2>/dev/null || true
            chown "${TARGET_USER}:${TARGET_USER}" "${USER_DESKTOP_FOLDER}/Ears Audio Captioner.desktop" 2>/dev/null || true
            chmod +x "${USER_DESKTOP_FOLDER}/Ears Audio Captioner.desktop" 2>/dev/null || true
            record_manifest "FILE:${USER_DESKTOP_FOLDER}/Ears Audio Captioner.desktop"
        fi
    fi
    if [ "$NO_AUTOSTART" -eq 0 ]; then
        cat > "${AUTOSTART_DIR}/ears.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Ears Audio Captioner
Comment=Zero-Gap RTSP Audio Captioning Utility (System Tray)
Exec=${VENV_PY} ${APP_DIR}/ears.py --tray
Path=${APP_DIR}
Terminal=false
Categories=AudioVideo;Utility;
EOF
        chown "${TARGET_USER}:${TARGET_USER}" "${AUTOSTART_DIR}/ears.desktop" 2>/dev/null || true
        chmod +x "${AUTOSTART_DIR}/ears.desktop" || true
        record_manifest "FILE:${AUTOSTART_DIR}/ears.desktop"
    fi
    cat > "${DESKTOP_DIR}/ears-uninstall.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Uninstall Ears Audio Captioner
Comment=Cleanly uninstall Ears Audio Captioner
Exec=bash "${APP_DIR}/packaging/linux/gui_linux.sh" uninstall
Path=${APP_DIR}
Terminal=false
Categories=AudioVideo;Utility;
EOF
    chown "${TARGET_USER}:${TARGET_USER}" "${DESKTOP_DIR}/ears-uninstall.desktop" 2>/dev/null || true
    chmod +x "${DESKTOP_DIR}/ears-uninstall.desktop" "${APP_DIR}/packaging/linux/gui_linux.sh" || true
    record_manifest "FILE:${DESKTOP_DIR}/ears-uninstall.desktop"
fi

chmod +x "${APP_DIR}/ears.py" "${APP_DIR}/uninstall.sh" 2>/dev/null || true

# If running inside an active Ubuntu/Linux GUI desktop session, launch the persistent System Tray companion now
if [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
    pkill -f "ears.py --tray" 2>/dev/null || true
    echo -e "${CYAN}  -> Desktop GUI detected! Launching persistent System Tray icon...${NC}"
    nohup "${VENV_PY}" "${APP_DIR}/ears.py" --tray >/dev/null 2>&1 &
fi

LAN_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo ""
echo -e "${CYAN}==============================================================================${NC}"
echo -e "${GREEN}  EARS INSTALLATION COMPLETE!${NC}"
echo -e "${CYAN}==============================================================================${NC}"
echo -e "  Systemd Service  : ${GREEN}Active (ears.service)${NC}"
echo -e "  Install Manifest : ${MANIFEST_FILE}"
echo -e "  Local Web UI     : http://127.0.0.1:8765"
if [ -n "$LAN_IP" ]; then
    echo -e "  LAN Web UI       : http://${LAN_IP}:8765"
fi
echo -e "  View Live Logs   : sudo journalctl -u ears.service -f"
echo -e "  Clean Uninstall  : ./uninstall.sh  (or ./uninstall.sh --purge)"
echo -e "${CYAN}==============================================================================${NC}"

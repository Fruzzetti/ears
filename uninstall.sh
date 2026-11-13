#!/usr/bin/env bash
# ==============================================================================
# EARS — Clean, Manifest-Aware Linux Uninstaller
# Reads `.install_manifest` so it ONLY removes system packages that Ears itself
# added during installation, leaving pre-existing system packages untouched.
# ==============================================================================
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Automatically delegate to macOS uninstaller when run on Darwin (macOS)
if [ "$(uname -s)" = "Darwin" ]; then
    exec bash "${APP_DIR}/packaging/macos/uninstall_macos.sh" "$@"
fi

for arg in "$@"; do
    if [ "$arg" = "--gui" ]; then
        exec bash "${APP_DIR}/packaging/linux/gui_linux.sh" uninstall
    fi
done

MANIFEST_FILE="${APP_DIR}/.install_manifest"
TARGET_USER="${SUDO_USER:-$USER}"
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
if [ -z "$TARGET_HOME" ]; then
    TARGET_HOME="$HOME"
fi

PURGE_DATA=0
KEEP_DATA=0
FORCE_REMOVE_SYS=0
for arg in "$@"; do
    if [ "$arg" = "--purge" ] || [ "$arg" = "-y" ]; then
        PURGE_DATA=1
    elif [ "$arg" = "--keep-data" ]; then
        KEEP_DATA=1
    elif [ "$arg" = "--remove-sys-pkgs" ]; then
        FORCE_REMOVE_SYS=1
    fi
done

echo -e "${CYAN}==============================================================================${NC}"
echo -e "${CYAN}  EARS — MANIFEST-AWARE LINUX UNINSTALLER${NC}"
echo -e "${CYAN}==============================================================================${NC}"

# 1. Stop and remove systemd service
echo -e "${GREEN}[1/5] Stopping and removing systemd service (ears.service)...${NC}"
if systemctl list-unit-files 2>/dev/null | grep -q "ears.service"; then
    sudo systemctl stop ears.service 2>/dev/null || true
    sudo systemctl disable ears.service 2>/dev/null || true
fi
if [ -f "/etc/systemd/system/ears.service" ]; then
    sudo rm -f "/etc/systemd/system/ears.service"
    sudo systemctl daemon-reload 2>/dev/null || true
fi

# 2. Terminate any manual ears.py or temporary buffer processes
echo -e "${GREEN}[2/5] Cleaning up running processes and temporary RAM buffers...${NC}"
pkill -f "ears.py" 2>/dev/null || true
pkill -f "ears_buffers" 2>/dev/null || true
rm -rf /dev/shm/ears_buffers /tmp/ears_buffers 2>/dev/null || true

# 3. Remove Desktop & Autostart entries
echo -e "${GREEN}[3/5] Removing desktop launcher and login autostart entries...${NC}"
rm -f "${TARGET_HOME}/.config/autostart/ears.desktop" 2>/dev/null || true
rm -f "${TARGET_HOME}/.local/share/applications/ears.desktop" 2>/dev/null || true
rm -f "${TARGET_HOME}/.local/share/applications/ears-uninstall.desktop" 2>/dev/null || true

# 4. Remove Isolated Python Virtualenv, Downloaded Model Cache & Manifest-Tracked System Packages
echo -e "${GREEN}[4/5] Removing Python virtual environment, model cache & checking `.install_manifest`...${NC}"
rm -rf "${APP_DIR}/venv" "${APP_DIR}/.venv" "${APP_DIR}/data/models" 2>/dev/null || true

if [ -f "$MANIFEST_FILE" ]; then
    APT_ADDED=()
    DNF_ADDED=()
    PACMAN_ADDED=()
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            APT_PKG:*)    APT_ADDED+=("${line#APT_PKG:}") ;;
            DNF_PKG:*)    DNF_ADDED+=("${line#DNF_PKG:}") ;;
            PACMAN_PKG:*) PACMAN_ADDED+=("${line#PACMAN_PKG:}") ;;
        esac
    done < "$MANIFEST_FILE"

    TOTAL_SYS_PKGS=$(( ${#APT_ADDED[@]} + ${#DNF_ADDED[@]} + ${#PACMAN_ADDED[@]} ))
    if [ "$TOTAL_SYS_PKGS" -gt 0 ]; then
        echo -e "${CYAN}  -> Manifest shows Ears installed these system packages: ${APT_ADDED[*]} ${DNF_ADDED[*]} ${PACMAN_ADDED[*]}${NC}"
        echo -e "${CYAN}     (Pre-existing system packages were not recorded and will never be touched.)${NC}"
        REMOVE_SYS=0
        if [ "$PURGE_DATA" -eq 1 ] || [ "$FORCE_REMOVE_SYS" -eq 1 ]; then
            REMOVE_SYS=1
        elif [ -t 0 ]; then
            read -r -p "Remove the system packages that Ears added? [Y/n]: " sresp
            if [[ ! "$sresp" =~ ^[Nn]$ ]]; then
                REMOVE_SYS=1
            fi
        fi

        if [ "$REMOVE_SYS" -eq 1 ]; then
            if [ "${#APT_ADDED[@]}" -gt 0 ]; then
                sudo apt-get remove --auto-remove -y "${APT_ADDED[@]}" || true
            fi
            if [ "${#DNF_ADDED[@]}" -gt 0 ]; then
                sudo dnf remove -y "${DNF_ADDED[@]}" || true
            fi
            if [ "${#PACMAN_ADDED[@]}" -gt 0 ]; then
                sudo pacman -Rs --noconfirm "${PACMAN_ADDED[@]}" || true
            fi
        fi
    else
        echo -e "  -> All system packages (python3, ffmpeg, etc.) were already present before Ears; leaving them intact."
    fi
    rm -f "$MANIFEST_FILE"
else
    echo -e "  -> No `.install_manifest` found; leaving all system packages intact."
fi

# 5. Optionally Purge Transcripts & Config
echo -e "${GREEN}[5/5] Checking user transcripts and configuration...${NC}"
if [ "$PURGE_DATA" -eq 1 ]; then
    echo -e "${YELLOW}  -> --purge specified: Removing config.json and data/ folder...${NC}"
    rm -rf "${APP_DIR}/data" "${APP_DIR}/config.json" 2>/dev/null || true
elif [ "$KEEP_DATA" -eq 1 ]; then
    echo -e "${CYAN}  -> Preserved your transcripts and config.json in ${APP_DIR}.${NC}"
else
    if [ -t 0 ]; then
        read -r -p "Do you also want to delete saved transcripts, audio clips, and config.json? [y/N]: " resp
        if [[ "$resp" =~ ^[Yy]$ ]]; then
            rm -rf "${APP_DIR}/data" "${APP_DIR}/config.json" 2>/dev/null || true
            echo -e "${YELLOW}  -> Deleted data/ and config.json.${NC}"
        else
            echo -e "${CYAN}  -> Preserved your transcripts and config.json in ${APP_DIR}.${NC}"
        fi
    else
        echo -e "${CYAN}  -> Preserved transcripts and config.json (pass --purge to delete them).${NC}"
    fi
fi

echo ""
echo -e "${GREEN}Ears has been cleanly uninstalled.${NC}"

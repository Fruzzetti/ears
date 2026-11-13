#!/usr/bin/env bash
# ==============================================================================
# EARS — Clean, Manifest-Aware macOS Uninstaller
# Reads `.install_manifest` so it ONLY offers to remove Homebrew packages that
# Ears itself installed, leaving pre-existing Homebrew packages untouched.
# ==============================================================================
set -e

GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
NC='\033[0m'

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "${SCRIPT_DIR}/../.." && pwd)"
MANIFEST_FILE="${APP_DIR}/.install_manifest"

PURGE_DATA=0
KEEP_DATA=0
for arg in "$@"; do
    if [ "$arg" = "--purge" ] || [ "$arg" = "-y" ]; then
        PURGE_DATA=1
    elif [ "$arg" = "--keep-data" ]; then
        KEEP_DATA=1
    fi
done

echo -e "${CYAN}==============================================================================${NC}"
echo -e "${CYAN}  EARS — MANIFEST-AWARE MACOS UNINSTALLER${NC}"
echo -e "${CYAN}==============================================================================${NC}"

# 1. Stop running processes & unload LaunchAgent
echo -e "${GREEN}[1/5] Stopping running Ears processes & LaunchAgent...${NC}"
PLIST="${HOME}/Library/LaunchAgents/com.ears.captioner.plist"
if [ -f "$PLIST" ]; then
    launchctl unload "$PLIST" 2>/dev/null || true
    rm -f "$PLIST"
fi
pkill -f "ears.py" 2>/dev/null || true
pkill -f "ears_buffers" 2>/dev/null || true

# 2. Remove /Applications/Ears.app & /Applications/Uninstall Ears.app
echo -e "${GREEN}[2/5] Removing Ears.app bundle...${NC}"
rm -rf "/Applications/Ears.app" "${HOME}/Applications/Ears.app" "/Applications/Uninstall Ears.app" "${HOME}/Applications/Uninstall Ears.app" 2>/dev/null || true

# 3. Clean temporary buffers, downloaded model cache & virtualenv
echo -e "${GREEN}[3/5] Cleaning temporary audio buffers, model cache & Python virtualenv...${NC}"
rm -rf /tmp/ears_buffers "${APP_DIR}/venv" "${APP_DIR}/.venv" "${APP_DIR}/data/models" 2>/dev/null || true

# 4. Check `.install_manifest` for Homebrew packages installed by Ears
echo -e "${GREEN}[4/5] Checking `.install_manifest` for Homebrew packages added by Ears...${NC}"
if [ -f "$MANIFEST_FILE" ]; then
    BREW_ADDED=()
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            BREW_PKG:*) BREW_ADDED+=("${line#BREW_PKG:}") ;;
        esac
    done < "$MANIFEST_FILE"

    if [ "${#BREW_ADDED[@]}" -gt 0 ]; then
        echo -e "${CYAN}  -> Manifest shows Ears installed these Homebrew packages: ${BREW_ADDED[*]}${NC}"
        REMOVE_BREW=0
        if [ "$PURGE_DATA" -eq 1 ]; then
            REMOVE_BREW=1
        elif [ -t 0 ]; then
            read -r -p "Uninstall the Homebrew packages that Ears added (${BREW_ADDED[*]})? [y/N]: " bresp
            if [[ "$bresp" =~ ^[Yy]$ ]]; then
                REMOVE_BREW=1
            fi
        fi
        if [ "$REMOVE_BREW" -eq 1 ] && command -v brew >/dev/null 2>&1; then
            brew uninstall "${BREW_ADDED[@]}" || true
        fi
    else
        echo -e "  -> ffmpeg and python3 were already present before Ears; leaving them intact."
    fi
    rm -f "$MANIFEST_FILE"
else
    echo -e "  -> No `.install_manifest` found; leaving Homebrew packages intact."
fi

# 5. Optional Purge of Transcripts & Config
echo -e "${GREEN}[5/5] Checking user transcripts and configuration...${NC}"
if [ "$PURGE_DATA" -eq 1 ]; then
    rm -rf "${APP_DIR}/data" "${APP_DIR}/config.json" 2>/dev/null || true
    echo -e "${YELLOW}  -> Deleted data/ and config.json.${NC}"
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
    fi
fi

echo ""
echo -e "${GREEN}Ears has been cleanly uninstalled from macOS.${NC}"

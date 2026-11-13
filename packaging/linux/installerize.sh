#!/usr/bin/env bash
# ==============================================================================
# EARS — Self-Extracting Linux Bundle Builder (`ears-installer.run`)
# Packages the Ears source tree into a single portable `.run` installer.
# ==============================================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OUTPUT_RUN="${PROJECT_ROOT}/dist/ears-installer.run"

mkdir -p "${PROJECT_ROOT}/dist"
TMP_PAYLOAD="$(mktemp /tmp/ears_payload_XXXXXX.tar.gz)"

echo "[1/3] Creating compressed archive of Ears source tree..."
tar -czf "$TMP_PAYLOAD" \
    --exclude="./venv" \
    --exclude="./.venv" \
    --exclude="./dist" \
    --exclude="./data" \
    --exclude="./config.json" \
    --exclude="./.git" \
    --exclude="__pycache__" \
    -C "$PROJECT_ROOT" .

echo "[2/3] Generating self-extracting header..."
cat > "$OUTPUT_RUN" <<'HEADER_EOF'
#!/usr/bin/env bash
set -e

INSTALL_DIR="${EARS_INSTALL_DIR:-$HOME/ears}"
if [ "${1:-}" = "--uninstall" ]; then
    if [ -x "${INSTALL_DIR}/uninstall.sh" ]; then
        exec "${INSTALL_DIR}/uninstall.sh" "${@:2}"
    else
        echo "Ears is not installed in ${INSTALL_DIR}."
        exit 1
    fi
fi

echo "=============================================================================="
echo "  EARS — SELF-EXTRACTING LINUX INSTALLER"
echo "  Extracting to: ${INSTALL_DIR}"
echo "=============================================================================="
mkdir -p "$INSTALL_DIR"

ARCHIVE_LINE="$(awk '/^__EARS_PAYLOAD_BELOW__$/ {print NR + 1; exit 0; }' "$0")"
tail -n +"$ARCHIVE_LINE" "$0" | tar -xz -C "$INSTALL_DIR"

chmod +x "${INSTALL_DIR}/install.sh" "${INSTALL_DIR}/uninstall.sh" "${INSTALL_DIR}/ears.py"
exec "${INSTALL_DIR}/install.sh" "$@"
__EARS_PAYLOAD_BELOW__
HEADER_EOF

cat "$TMP_PAYLOAD" >> "$OUTPUT_RUN"
chmod +x "$OUTPUT_RUN"
rm -f "$TMP_PAYLOAD"

SIZE_HUMAN="$(du -h "$OUTPUT_RUN" | awk '{print $1}')"
echo "[3/3] Built single-file Linux installer: ${OUTPUT_RUN} (${SIZE_HUMAN})"
echo "      Run with: ./dist/ears-installer.run"
echo "      Uninstall: ./dist/ears-installer.run --uninstall"

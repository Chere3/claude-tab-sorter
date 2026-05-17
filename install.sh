#!/usr/bin/env bash
set -euo pipefail

EXT_ID="${1:-}"
BROWSER="${2:-chrome}"

if [ -z "$EXT_ID" ]; then
  cat >&2 <<USAGE
Uso: $0 <EXTENSION_ID> [browser]

  EXTENSION_ID   ID que aparece en chrome://extensions tras cargar la extensión
                 sin empaquetar (modo desarrollador).
  browser        chrome (default), brave, edge, arc, chromium

Ejemplo:
  $0 abcdefghijklmnopabcdefghijklmnop chrome
USAGE
  exit 1
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HOST_SCRIPT="$SCRIPT_DIR/native-host/host.sh"

if [ ! -f "$HOST_SCRIPT" ]; then
  echo "No encuentro $HOST_SCRIPT" >&2
  exit 1
fi

chmod +x "$HOST_SCRIPT" "$SCRIPT_DIR/native-host/host.js"

case "$BROWSER" in
  chrome)    TARGET="$HOME/Library/Application Support/Google/Chrome/NativeMessagingHosts" ;;
  brave)     TARGET="$HOME/Library/Application Support/BraveSoftware/Brave-Browser/NativeMessagingHosts" ;;
  edge)      TARGET="$HOME/Library/Application Support/Microsoft Edge/NativeMessagingHosts" ;;
  arc)       TARGET="$HOME/Library/Application Support/Arc/User Data/NativeMessagingHosts" ;;
  chromium)  TARGET="$HOME/Library/Application Support/Chromium/NativeMessagingHosts" ;;
  *)
    echo "Browser desconocido: $BROWSER" >&2
    exit 1
    ;;
esac

mkdir -p "$TARGET"
MANIFEST="$TARGET/com.diego.tabsorter.json"

cat > "$MANIFEST" <<JSON
{
  "name": "com.diego.tabsorter",
  "description": "Tab Sorter native host (invoca el binario de Claude Code)",
  "path": "$HOST_SCRIPT",
  "type": "stdio",
  "allowed_origins": [
    "chrome-extension://$EXT_ID/"
  ]
}
JSON

echo "Instalado native messaging host:"
echo "  manifest: $MANIFEST"
echo "  host:     $HOST_SCRIPT"
echo "  ext-id:   $EXT_ID"
echo ""
echo "Reinicia $BROWSER para que detecte el host."

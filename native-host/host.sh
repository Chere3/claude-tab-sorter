#!/bin/bash
# Wrapper para Native Messaging: las apps GUI en macOS no tienen homebrew
# en PATH, así que lo añadimos antes de exec'ear node.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$PATH"
DIR="$(cd "$(dirname "$0")" && pwd)"
exec node "$DIR/host.js"

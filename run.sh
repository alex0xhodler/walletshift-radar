#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

PYTHON="$DIR/.venv/bin/python"
[ -x "$PYTHON" ] || PYTHON="python3"

exec "$PYTHON" -m walletshift_radar.main \
  --alchemy "dKXngnd_Ab-UtHfz-Pw8V" \
  "$@"

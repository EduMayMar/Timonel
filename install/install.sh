#!/bin/sh
set -eu

SOURCE="${TIMONEL_SOURCE:-timonel}"

say() { printf '%s\n' "$*"; }

pick_python() {
  for candidate in python3 python; do
    if command -v "$candidate" >/dev/null 2>&1 &&
      "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
      echo "$candidate"
      return 0
    fi
  done
  return 1
}

say "Timonel installer"
say "This script installs Timonel for your user only. It never uses sudo and changes nothing else."
say ""

if command -v uv >/dev/null 2>&1; then
  set -- uv tool install "$SOURCE"
elif command -v pipx >/dev/null 2>&1; then
  set -- pipx install "$SOURCE"
elif PYTHON=$(pick_python); then
  say "Neither uv nor pipx was found. The safest option is pipx (isolated per-user installs):"
  say "  $PYTHON -m pip install --user pipx && $PYTHON -m pipx ensurepath"
  say "Then run this script again. Guide: https://pipx.pypa.io/stable/installation/"
  exit 1
else
  say "Python 3.11 or newer was not found. Install it first: https://www.python.org/downloads/"
  exit 1
fi

say "It will run:"
say "  $*"
say ""
printf 'Continue? [y/N] '
answer=""
if [ -t 0 ]; then
  read -r answer || true
elif { true </dev/tty; } 2>/dev/null; then
  read -r answer </dev/tty || true
else
  read -r answer || true
fi
case "$answer" in
  y|Y|yes|YES|s|S|si|sí) ;;
  *) say "Nothing was changed."; exit 0 ;;
esac

"$@"
say ""
say "Done. Open a new terminal if 'timonel' is not found, then run:"
say "  timonel start"

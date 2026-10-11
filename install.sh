#!/usr/bin/env bash
# Install g923-claude: virtualenv, Claude Code hooks, systemd user service.
# ./install.sh --hud also installs and starts the floating HUD (pulls in Qt, ~250 MB).
set -euo pipefail
hud=false
[ "${1:-}" = "--hud" ] && hud=true
repo="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$repo"

if [ ! -x .venv/bin/python ]; then
  if command -v uv >/dev/null; then
    uv venv -q .venv && uv pip install -q --python .venv/bin/python -r requirements.txt
  else
    python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt
  fi
fi

[ -w /dev/uinput ] || echo "warning: /dev/uinput is not writable by $USER; see README > Troubleshooting"

.venv/bin/python -m g923.hooks

unit_dir="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
mkdir -p "$unit_dir"
sed "s|@REPO@|$repo|g" systemd/g923d.service > "$unit_dir/g923d.service"
if $hud; then
  if command -v uv >/dev/null; then
    uv pip install -q --python .venv/bin/python PySide6-Essentials
  else
    .venv/bin/pip install -q PySide6-Essentials
  fi
  sed "s|@REPO@|$repo|g" systemd/g923-hud.service > "$unit_dir/g923-hud.service"
fi
systemctl --user daemon-reload
systemctl --user enable --now g923d.service
$hud && systemctl --user enable --now g923-hud.service && echo "HUD running in the bottom-left corner."
echo "g923d running. Focus your Claude Code terminal and press PS to arm."

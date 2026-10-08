#!/usr/bin/env bash
# Remove the g923-claude service and hooks (the repo itself is left alone).
set -euo pipefail
repo="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
cd "$repo"
systemctl --user disable --now g923d.service 2>/dev/null || true
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/g923d.service"
systemctl --user daemon-reload
.venv/bin/python -m g923.hooks --remove

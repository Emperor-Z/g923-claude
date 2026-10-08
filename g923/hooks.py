"""Add or remove g923-claude hooks in ~/.claude/settings.json.

Only entries whose command runs g923-ping are touched; other hooks are kept.
A backup is written next to the settings file before any change.
"""

import argparse
import json
import shutil
import sys
from pathlib import Path

SETTINGS = Path.home() / ".claude" / "settings.json"
EVENTS = {
    "UserPromptSubmit": "busy",
    "Stop": "done",
    "PermissionRequest": "attention",
    "PostToolUse": "clear",
}


def is_ours(group):
    return any("g923-ping" in h.get("command", "") for h in group.get("hooks", []))


def update(settings, ping, install):
    hooks = settings.setdefault("hooks", {})
    for event in list(hooks):
        hooks[event] = [g for g in hooks[event] if not is_ours(g)]
        if not hooks[event]:
            del hooks[event]
    if install:
        for event, msg in EVENTS.items():
            hooks.setdefault(event, []).append({
                "matcher": "",
                "hooks": [{"type": "command", "command": f'"{ping}" {msg}', "timeout": 2}],
            })
    if not hooks:
        del settings["hooks"]
    return settings


def ping_path():
    """Prefer the checkout's standalone script; fall back to the installed one."""
    checkout = Path(__file__).resolve().parent.parent / "bin" / "g923-ping"
    if checkout.exists():
        return checkout
    found = shutil.which("g923-ping")
    if not found:
        sys.exit("g923-ping not found on PATH")
    return Path(found)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--remove", action="store_true")
    ap.add_argument("--settings", type=Path, default=SETTINGS)
    args = ap.parse_args()
    ping = ping_path()
    settings = json.loads(args.settings.read_text()) if args.settings.exists() else {}
    if args.settings.exists():
        shutil.copy2(args.settings, args.settings.with_name(args.settings.name + ".g923.bak"))
    update(settings, ping, install=not args.remove)
    args.settings.parent.mkdir(parents=True, exist_ok=True)
    args.settings.write_text(json.dumps(settings, indent=2) + "\n")
    print(("removed" if args.remove else "installed") + f" g923 hooks in {args.settings}")


if __name__ == "__main__":
    main()

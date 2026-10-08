"""Tell g923d what Claude Code is doing. Called from Claude Code hooks.

Usage: g923-ping busy|done|attention|clear

Never fails and never blocks for long, so a stopped daemon can't slow
Claude Code down. bin/g923-ping is a standalone copy that runs without the
virtualenv, which keeps hook startup fast.
"""

import os
import socket
import sys


def main():
    path = os.environ.get("G923_SOCKET") or os.path.join(
        os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"), "g923.sock")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            s.connect(path)
            s.sendall(" ".join(sys.argv[1:]).encode())
    except OSError:
        pass


if __name__ == "__main__":
    main()

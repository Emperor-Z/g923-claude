"""Configuration loading.

The repo's config.toml (or the copy bundled in the wheel) holds every default.
A user or wheel config is layered on top, so it only needs the keys it changes.
"""

import os
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
DEFAULTS = [
    PACKAGE.parent / "config.toml",         # running from a git checkout
    PACKAGE / "default_config.toml",        # installed wheel
]
USER = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "g923-claude" / "config.toml"


def defaults_path():
    for path in DEFAULTS:
        if path.exists():
            return path
    raise FileNotFoundError("default config.toml is missing")


def merge(base, over):
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out


def read(path):
    with open(path, "rb") as f:
        return tomllib.load(f)


def load(path=None):
    """Defaults, then the given file ($G923_CONFIG or the user config) on top."""
    base = defaults_path()
    cfg = read(base)
    override = Path(path) if path else (
        Path(os.environ["G923_CONFIG"]) if os.environ.get("G923_CONFIG") else USER)
    if override.exists() and override.resolve() != base.resolve():
        cfg = merge(cfg, read(override))
    # TOML keys are strings; button codes are ints.
    cfg["buttons"] = {int(code): name for code, name in cfg["buttons"].items()}
    return cfg


def expand(path):
    return os.path.expanduser(os.path.expandvars(path))

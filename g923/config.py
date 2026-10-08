import os
import tomllib
from pathlib import Path

PACKAGE = Path(__file__).resolve().parent
SEARCH = [
    Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "g923-claude" / "config.toml",
    PACKAGE.parent / "config.toml",         # running from a git checkout
    PACKAGE / "default_config.toml",        # installed wheel
]


def find():
    if os.environ.get("G923_CONFIG"):
        return Path(os.environ["G923_CONFIG"])
    for path in SEARCH:
        if path.exists():
            return path
    raise FileNotFoundError("no config.toml found; set G923_CONFIG")


def load(path=None):
    path = Path(path) if path else find()
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    # TOML keys are strings; button codes are ints.
    cfg["buttons"] = {int(code): name for code, name in cfg["buttons"].items()}
    return cfg


def expand(path):
    return os.path.expanduser(os.path.expandvars(path))

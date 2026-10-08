import os
import tomllib
from pathlib import Path

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config.toml"


def load(path=None):
    path = Path(path or os.environ.get("G923_CONFIG") or DEFAULT_PATH)
    with open(path, "rb") as f:
        cfg = tomllib.load(f)
    # TOML keys are strings; button codes are ints.
    cfg["buttons"] = {int(code): name for code, name in cfg["buttons"].items()}
    return cfg


def expand(path):
    return os.path.expanduser(os.path.expandvars(path))

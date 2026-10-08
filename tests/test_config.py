import tempfile
import unittest
from pathlib import Path

from g923 import config

REPO = Path(__file__).resolve().parent.parent


class ConfigTest(unittest.TestCase):
    def test_override_layers_over_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".toml") as f:
            f.write('[device]\nname_match = "G29"\n[buttons]\n999 = "ps"\n[ffb]\nstrength = 0.2\n')
            f.flush()
            cfg = config.load(f.name)
        self.assertEqual(cfg["device"]["name_match"], "G29")
        self.assertEqual(cfg["buttons"][999], "ps")
        self.assertEqual(cfg["buttons"][288], "cross")      # default kept
        self.assertEqual(cfg["ffb"]["strength"], 0.2)
        self.assertEqual(cfg["ffb"]["autocenter"], 0.15)   # default kept

    def test_every_shipped_config_is_valid(self):
        defaults = config.load(REPO / "config.toml")
        known = set(defaults["buttons"].values())
        for path in sorted((REPO / "configs").glob("*.toml")):
            with self.subTest(config=path.name):
                cfg = config.load(path)
                self.assertTrue(cfg["device"]["name_match"])
                self.assertLessEqual(set(cfg["drive"]) - {"dpad_up", "dpad_down", "dpad_left", "dpad_right"},
                                     known | set(cfg["buttons"].values()))


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from types import SimpleNamespace

from g923 import config, hud
from tests.test_drive import REPO_CONFIG

try:
    import PySide6  # noqa: F401
    HAVE_QT = True
except ImportError:
    HAVE_QT = False


class HelpersTest(unittest.TestCase):
    def test_model_names(self):
        self.assertEqual(hud.model_name("opus"), "OPUS")
        self.assertEqual(hud.model_name("opus[1m]"), "OPUS 1M")
        self.assertEqual(hud.model_name("claude-sonnet-5-5"), "SONNET 5.5")
        self.assertEqual(hud.model_name("claude-haiku-4-5-20251001"), "HAIKU 4.5")
        self.assertEqual(hud.model_name("claude-opus-5-5[1m]"), "OPUS 5.5 1M")
        self.assertEqual(hud.model_name(None), "—")

    def test_clock(self):
        self.assertEqual(hud.clock(0), "0:00")
        self.assertEqual(hud.clock(75.9), "1:15")

    def test_demo_covers_every_state(self):
        seen = {hud.demo_state(t / 2)["claude"] for t in range(48)}
        modes = {hud.demo_state(t / 2)["mode"] for t in range(48)}
        self.assertEqual(seen, {"idle", "busy", "attention"})
        self.assertEqual(modes, {"drive", "type"})


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RenderTest(unittest.TestCase):
    def test_every_demo_frame_renders(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cfg = config.load(REPO_CONFIG)
        with tempfile.TemporaryDirectory() as tmp:
            for t in (1, 3.5, 7, 11, 15, 19.5):
                png = os.path.join(tmp, f"{t}.png")
                hud.build(SimpleNamespace(screenshot=png, at=t, demo=False), cfg)
                self.assertGreater(os.path.getsize(png), 1000)


if __name__ == "__main__":
    unittest.main()

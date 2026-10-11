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
        self.assertEqual(hud.model_name("opus"), "Opus")
        self.assertEqual(hud.model_name("opus[1m]"), "Opus 1M")
        self.assertEqual(hud.model_name("claude-sonnet-5-5"), "Sonnet 5.5")
        self.assertEqual(hud.model_name("claude-haiku-4-5-20251001"), "Haiku 4.5")
        self.assertEqual(hud.model_name("claude-opus-5-5[1m]"), "Opus 5.5 1M")
        self.assertEqual(hud.model_name(None), "No model")

    def test_headline_for_each_tool(self):
        self.assertEqual(hud.doing({"name": "Edit", "detail": "hud.py"}), "Editing hud.py")
        self.assertEqual(hud.doing({"name": "Bash", "detail": "npm test"}), "Running npm test")
        self.assertEqual(hud.doing({"name": "Grep", "detail": "watch"}), "Searching “watch”")
        self.assertEqual(hud.doing({"name": "Read", "detail": ""}), "Reading…")
        self.assertEqual(hud.doing({"name": "mcp__github__create_issue"}), "Using github")
        self.assertEqual(hud.doing({"name": "Frobnicate"}), "Using Frobnicate")

    def test_details_can_be_hidden(self):
        self.assertEqual(hud.doing({"name": "Bash", "detail": "rm -rf secrets"}, details=False), "Running…")

    def test_clock(self):
        self.assertEqual(hud.clock(0), "0:00")
        self.assertEqual(hud.clock(75.9), "1:15")

    def test_demo_covers_every_state(self):
        frames = [hud.demo_state(t / 2) for t in range(48)]
        self.assertEqual({f["claude"] for f in frames}, {"idle", "busy", "attention"})
        self.assertEqual({f["mode"] for f in frames}, {"drive", "type"})
        self.assertTrue(any(f["tool"] for f in frames) and any(f["approval"] for f in frames))


@unittest.skipUnless(HAVE_QT, "PySide6 not installed")
class RenderTest(unittest.TestCase):
    def test_every_demo_frame_renders(self):
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
        cfg = config.load(REPO_CONFIG)
        with tempfile.TemporaryDirectory() as tmp:
            for t in (1, 6, 9.6, 11, 16, 20):
                png = os.path.join(tmp, f"{t}.png")
                hud.build(SimpleNamespace(screenshot=png, at=t, demo=False), cfg)
                self.assertGreater(os.path.getsize(png), 1000)


if __name__ == "__main__":
    unittest.main()

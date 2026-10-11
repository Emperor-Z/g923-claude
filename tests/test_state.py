import asyncio
import json
import os
import tempfile
import unittest

from g923 import config
from g923.daemon import Daemon
from tests.test_drive import REPO_CONFIG, Recorder, SilentFeedback, axis, key, make, tap

GEAR_3, DIAL_CW, OPTIONS = 302, 709, 297


def shift_with_clutch(d, gear_code):
    axis(d, "ABS_Y", 255)
    axis(d, "ABS_Y", 0)
    key(d, gear_code, True)
    axis(d, "ABS_Y", 255)


class SnapshotTest(unittest.TestCase):
    def test_gear_shows_while_the_shifter_is_in_it(self):
        d = make()
        key(d, GEAR_3, True)
        self.assertEqual(d.snapshot()["gear"], "3")
        key(d, GEAR_3, False)
        self.assertIsNone(d.snapshot()["gear"])

    def test_gear_shows_even_when_disarmed(self):
        d = make(armed=False)
        key(d, GEAR_3, True)
        self.assertEqual(d.snapshot()["gear"], "3")

    def test_shifting_sets_the_model(self):
        d = make()
        shift_with_clutch(d, GEAR_3)
        self.assertEqual(d.snapshot()["model"], "opus")

    def test_grinding_keeps_the_model(self):
        d = make()
        d.model = "sonnet"
        tap(d, GEAR_3)  # no clutch
        self.assertEqual(d.snapshot()["model"], "sonnet")

    def test_claude_timing(self):
        d = make()
        d.hook_event("busy")
        started = d.snapshot()["busy_since"]
        self.assertIsNotNone(started)
        d.hook_event("attention")
        d.hook_event("busy")
        self.assertEqual(d.snapshot()["busy_since"], started)  # same turn, same clock
        d.hook_event("done")
        snap = d.snapshot()
        self.assertEqual(snap["claude"], "idle")
        self.assertIsNone(snap["busy_since"])
        self.assertIsNotNone(snap["done_at"])

    def test_pedals_and_steering(self):
        d = make()
        axis(d, "ABS_Z", 128)
        axis(d, "ABS_X", 32768 + 728)  # 10 degrees right
        snap = d.snapshot()
        self.assertAlmostEqual(snap["pedals"]["gas"], 0.5, places=2)
        self.assertEqual(snap["pedals"]["brake"], 0.0)
        self.assertEqual(snap["steer_deg"], 10.0)

    def test_type_mode_letter(self):
        d = make()
        tap(d, OPTIONS)
        tap(d, DIAL_CW)   # preview a
        tap(d, DIAL_CW)   # preview b
        t = d.snapshot()["type"]
        self.assertEqual((t["set"], t["preview"], t["chars"][t["idx"]]), ("abc", "b", "b"))

    def test_starts_from_claude_settings(self):
        cfg = config.load(REPO_CONFIG)
        d = Daemon(cfg, Recorder(), SilentFeedback(),
                   claude={"model": "sonnet", "effortLevel": "medium"})
        snap = d.snapshot()
        self.assertEqual((snap["model"], snap["effort"]), ("sonnet", "medium"))

    def test_unknown_effort_in_settings_falls_back(self):
        cfg = config.load(REPO_CONFIG)
        d = Daemon(cfg, Recorder(), SilentFeedback(), claude={"effortLevel": "turbo"})
        self.assertEqual(d.snapshot()["effort"], cfg["effort"]["start"])

    def test_a_turn_from_prompt_to_done(self):
        d = make()
        d.hook_event("busy", {"prompt": "fix the tests", "session": "my-app"})
        d.hook_event("tool", {"tool": "Read", "detail": "daemon.py"})
        self.assertEqual(d.snapshot()["tool"], {"name": "Read", "detail": "daemon.py"})
        d.hook_event("clear", {"tool": "Read"})
        d.hook_event("tool", {"tool": "Bash", "detail": "npm test"})
        d.hook_event("attention", {"tool": "Bash", "detail": "npm test"})
        snap = d.snapshot()
        self.assertEqual(snap["approval"], {"name": "Bash", "detail": "npm test"})
        self.assertEqual((snap["task"], snap["session"]), ("fix the tests", "my-app"))
        d.hook_event("clear", {"tool": "Bash"})
        d.hook_event("done")
        snap = d.snapshot()
        self.assertEqual((snap["trail"], snap["steps"]), (["Read", "Bash"], 2))
        self.assertIsNone(snap["tool"])
        self.assertIsNone(snap["approval"])
        self.assertEqual(snap["task"], "fix the tests")  # still shown with "done"

    def test_next_prompt_starts_a_fresh_trail(self):
        d = make()
        d.hook_event("busy", {"prompt": "one"})
        d.hook_event("clear", {"tool": "Read"})
        d.hook_event("done")
        d.hook_event("busy", {"prompt": "two"})
        snap = d.snapshot()
        self.assertEqual((snap["task"], snap["trail"], snap["steps"]), ("two", [], 0))

    def test_tool_without_a_prompt_still_counts_as_working(self):
        d = make()  # e.g. the daemon restarted mid-turn
        d.hook_event("tool", {"tool": "Edit", "detail": "hud.py"})
        self.assertEqual(d.snapshot()["claude"], "busy")
        self.assertIsNotNone(d.snapshot()["busy_since"])

    def test_snapshot_is_json(self):
        json.dumps(make().snapshot())


class WatchTest(unittest.TestCase):
    def test_watch_streams_changes(self):
        d = make()
        with tempfile.TemporaryDirectory() as tmp:
            sock = os.path.join(tmp, "g923.sock")
            d.cfg["feedback"]["socket"] = sock

            async def go():
                server = await d.serve_hooks()
                publisher = asyncio.create_task(d.publish_loop())
                reader, writer = await asyncio.open_unix_connection(sock)
                writer.write(b"watch")
                first = json.loads(await asyncio.wait_for(reader.readline(), 1))
                d.hook_event("busy")
                second = json.loads(await asyncio.wait_for(reader.readline(), 1))
                writer.close()
                await asyncio.sleep(0.1)
                watchers = len(d.watchers)
                publisher.cancel()
                server.close()
                return first, second, watchers

            first, second, watchers = asyncio.run(go())
        self.assertEqual((first["claude"], second["claude"]), ("idle", "busy"))
        self.assertEqual(watchers, 0)  # a closed HUD is forgotten


if __name__ == "__main__":
    unittest.main()

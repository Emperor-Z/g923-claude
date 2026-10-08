import unittest

from evdev import InputEvent
from evdev import ecodes as e

from g923 import config
from g923.daemon import Daemon


class Recorder:
    def __init__(self):
        self.log = []

    def press(self, combo):
        self.log.append(("key", combo))

    def hold(self, key, down):
        self.log.append(("hold", key, down))

    def type(self, text):
        self.log.append(("type", text))

    def scroll(self, notches):
        self.log.append(("scroll", notches))

    def pause(self, seconds):
        pass

    def close(self):
        pass


class SilentFeedback:
    def __init__(self):
        self.events = []

    def notify(self, title, body=""):
        self.events.append(title)

    def __getattr__(self, name):
        return lambda *a, **k: self.events.append(name)


def make(armed=True):
    cfg = config.load()
    d = Daemon(cfg, Recorder(), SilentFeedback())
    d.armed = armed
    return d


def key(d, code, down=True):
    d.handle(InputEvent(0, 0, e.EV_KEY, code, 1 if down else 0))


def tap(d, code):
    key(d, code, True)
    key(d, code, False)


def axis(d, name, value):
    d.handle(InputEvent(0, 0, e.EV_ABS, e.ecodes[name], value))


def pedal(d, name, values=(255, 0, 255)):
    for v in values:
        axis(d, name, v)


class DriveModeTest(unittest.TestCase):
    def test_pedals(self):
        d = make()
        pedal(d, "ABS_Z")
        pedal(d, "ABS_RZ")
        self.assertEqual(d.out.log, [("key", "enter"), ("key", "esc")])

    def test_pedal_needs_release_before_refiring(self):
        d = make()
        pedal(d, "ABS_Z", (255, 0, 60, 0, 60, 255, 0))
        # 60 is ~76% pressed, above release_at, so only two presses.
        self.assertEqual(d.out.log, [("key", "enter"), ("key", "enter")])

    def test_bogus_first_reading_does_not_fire(self):
        d = make()
        axis(d, "ABS_Z", 0)
        self.assertEqual(d.out.log, [])

    def test_clutch_tap_cycles_mode(self):
        d = make()
        pedal(d, "ABS_Y")
        self.assertEqual(d.out.log, [("key", "shift+tab")])

    def test_buttons(self):
        d = make()
        for code in (293, 292, 288, 290, 289, 291, 295, 294, 298):
            tap(d, code)
        self.assertEqual([x[1] for x in d.out.log],
                         ["up", "down", "enter", "esc", "shift+tab", "ctrl+t",
                          "ctrl+o", "ctrl+b", "tab"])

    def test_hold_button(self):
        d = make()
        tap(d, 299)
        self.assertEqual(d.out.log, [("hold", "space", True), ("hold", "space", False)])

    def test_dpad(self):
        d = make()
        for a, v in (("ABS_HAT0Y", -1), ("ABS_HAT0Y", 0), ("ABS_HAT0X", 1), ("ABS_HAT0X", 0)):
            axis(d, a, v)
        self.assertEqual(d.out.log, [("key", "up"), ("key", "right")])

    def test_disarmed_ignores_everything_but_ps(self):
        d = make(armed=False)
        pedal(d, "ABS_Z")
        tap(d, 288)
        self.assertEqual(d.out.log, [])
        tap(d, 712)
        self.assertTrue(d.armed)
        tap(d, 288)
        self.assertEqual(d.out.log, [("key", "enter")])

    def test_disarm_releases_held_keys(self):
        d = make()
        key(d, 299, True)
        tap(d, 712)
        self.assertEqual(d.out.log[-1], ("hold", "space", False))


class SteeringTest(unittest.TestCase):
    def run_scroll(self, value, seconds=1.0):
        import asyncio

        d = make()
        d.steer_value = value

        async def go():
            task = asyncio.create_task(d.scroll_loop())
            await asyncio.sleep(seconds)
            task.cancel()

        asyncio.run(go())
        return sum(x[1] for x in d.out.log if x[0] == "scroll")

    def test_centre_is_still(self):
        self.assertEqual(self.run_scroll(32768 + 500, 0.3), 0)

    def test_full_left_scrolls_up_fast(self):
        self.assertGreater(self.run_scroll(32768 - 72.8 * 120), 20)

    def test_right_scrolls_down(self):
        self.assertLess(self.run_scroll(32768 + 72.8 * 45), 0)


if __name__ == "__main__":
    unittest.main()

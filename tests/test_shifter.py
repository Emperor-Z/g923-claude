import unittest

from tests.test_drive import axis, key, make, tap

STASHED = [("type", "x"), ("key", "ctrl+s")]
RESTORED = [("key", "ctrl+s"), ("key", "backspace")]


def command(text):
    return [("type", text), ("key", "enter")]


class ShifterTest(unittest.TestCase):
    def shift_with_clutch(self, d, gear_code):
        axis(d, "ABS_Y", 255)
        axis(d, "ABS_Y", 0)       # clutch in
        key(d, gear_code, True)   # into gear
        axis(d, "ABS_Y", 255)     # clutch out

    def test_clutch_and_gear_changes_model(self):
        d = make()
        self.shift_with_clutch(d, 302)
        self.assertEqual(d.out.log, STASHED + command("/model opus") + RESTORED)

    def test_clutch_used_for_shift_does_not_cycle_mode(self):
        d = make()
        self.shift_with_clutch(d, 300)
        self.assertNotIn(("key", "shift+tab"), d.out.log)

    def test_gear_without_clutch_grinds(self):
        d = make()
        tap(d, 301)
        self.assertEqual(d.out.log, [])
        self.assertIn("grind", d.fb.events)

    def test_gear_5_sends_bracketed_alias(self):
        d = make()
        self.shift_with_clutch(d, 704)
        self.assertIn(("type", "/model opus[1m]"), d.out.log)

    def test_reverse_rewinds(self):
        d = make()
        self.shift_with_clutch(d, 706)
        self.assertEqual(d.out.log, [("key", "esc"), ("key", "esc")])

    def test_neutral_does_nothing(self):
        d = make()
        self.shift_with_clutch(d, 300)
        n = len(d.out.log)
        key(d, 300, False)  # back to neutral
        self.assertEqual(len(d.out.log), n)


class EffortTest(unittest.TestCase):
    def test_plus_and_minus_step_effort(self):
        d = make()  # starts at high
        tap(d, 707)
        tap(d, 708)
        tap(d, 708)
        sent = [x[1] for x in d.out.log if x[0] == "type" and x[1].startswith("/effort")]
        self.assertEqual(sent, ["/effort xhigh", "/effort high", "/effort medium"])

    def test_effort_clamps_at_max(self):
        d = make()
        for _ in range(5):
            tap(d, 707)
        sent = [x[1] for x in d.out.log if x[0] == "type" and x[1].startswith("/effort")]
        self.assertEqual(sent, ["/effort xhigh", "/effort max"])


if __name__ == "__main__":
    unittest.main()

import unittest

from evdev import ecodes as e

from g923.feedback import Feedback
from g923.ffb import EFFECTS, Wheel


class FakeDev:
    def __init__(self, ff=True, fail_after=None):
        self.ff = ff
        self.uploaded = []
        self.writes = []
        self.erased = []
        self.fail_after = fail_after

    def capabilities(self):
        return {e.EV_FF: [e.FF_CONSTANT, e.FF_PERIODIC, e.FF_GAIN, e.FF_AUTOCENTER]} if self.ff else {}

    def upload_effect(self, fx):
        self.uploaded.append(fx)
        return len(self.uploaded) - 1

    def write(self, etype, code, value):
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            raise OSError("gone")
        self.writes.append((etype, code, value))

    def erase_effect(self, eid):
        self.erased.append(eid)


CFG = {"strength": 0.5, "autocenter": 0.25}


class WheelTest(unittest.TestCase):
    def test_uploads_effects_and_sets_gain_and_autocenter(self):
        dev = FakeDev()
        w = Wheel(dev, CFG)
        self.assertTrue(w.ok)
        self.assertEqual(len(dev.uploaded), sum(len(v) for v in EFFECTS.values()))
        self.assertIn((e.EV_FF, e.FF_GAIN, int(0.5 * 0xFFFF)), dev.writes)
        self.assertIn((e.EV_FF, e.FF_AUTOCENTER, int(0.25 * 0xFFFF)), dev.writes)

    def test_play_done_triggers_both_kicks(self):
        dev = FakeDev()
        w = Wheel(dev, CFG)
        dev.writes.clear()
        self.assertTrue(w.play("done"))
        self.assertEqual(dev.writes, [(e.EV_FF, i, 1) for i in w.ids["done"]])

    def test_no_ffb_means_not_ok(self):
        self.assertFalse(Wheel(FakeDev(ff=False), CFG).ok)

    def test_unplugged_wheel_degrades(self):
        dev = FakeDev()
        w = Wheel(dev, CFG)
        dev.fail_after = len(dev.writes)
        self.assertFalse(w.play("done"))
        self.assertFalse(w.ok)

    def test_close_erases(self):
        dev = FakeDev()
        w = Wheel(dev, CFG)
        w.close()
        self.assertEqual(len(dev.erased), len(dev.uploaded))


class FeedbackFallbackTest(unittest.TestCase):
    def make(self, ff):
        fb = Feedback({"feedback": {"notify": False, "sound": True}})
        fb.sounds = []
        fb.sound = fb.sounds.append
        fb.attach_wheel(FakeDev(ff=ff), CFG)
        return fb

    def test_ffb_replaces_sound(self):
        fb = self.make(ff=True)
        fb.done()
        self.assertEqual(fb.sounds, [])

    def test_sound_without_ffb(self):
        fb = self.make(ff=False)
        fb.done()
        self.assertEqual(fb.sounds, ["done"])


if __name__ == "__main__":
    unittest.main()

import unittest
from collections import Counter

from pathlib import Path

from g923 import config
from g923.daemon import Daemon

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
from g923.typing import WordModel
from tests.test_drive import Recorder, SilentFeedback, axis, tap


class FixedModel(WordModel):
    def __init__(self, words):
        self.scores = Counter({w: len(words) - i for i, w in enumerate(words)})


def make(words=("refactor", "readme", "repo")):
    from g923.typing import Typer

    cfg = config.load(REPO_CONFIG)
    out, fb = Recorder(), SilentFeedback()
    d = Daemon(cfg, out, fb, typer=Typer(cfg, out, fb, FixedModel(list(words))))
    d.armed = True
    tap(d, 297)  # Options -> Type mode
    out.log.clear()
    return d


def screen(log):
    """Replay typed text and backspaces into the resulting prompt text."""
    text = ""
    for entry in log:
        if entry[0] == "type":
            text += entry[1]
        elif entry == ("key", "backspace"):
            text = text[:-1]
    return text


def dpad(d, direction):
    hat, v = {"left": ("ABS_HAT0X", -1), "right": ("ABS_HAT0X", 1),
              "up": ("ABS_HAT0Y", -1), "down": ("ABS_HAT0Y", 1)}[direction]
    axis(d, hat, v)
    axis(d, hat, 0)


DIAL_CW, DIAL_CCW, DIAL_ENTER = 709, 710, 711
CROSS, CIRCLE, SQUARE, TRIANGLE = 288, 290, 289, 291


class TypeModeTest(unittest.TestCase):
    def test_first_press_previews_e(self):
        d = make()
        dpad(d, "right")
        self.assertEqual(screen(d.out.log), "e")

    def test_scrolling_replaces_preview(self):
        d = make()
        for _ in range(3):
            tap(d, DIAL_CW)  # e, t, a
        self.assertEqual(screen(d.out.log), "a")
        dpad(d, "left")
        self.assertEqual(screen(d.out.log), "t")

    def test_commit_and_double_letter(self):
        d = make()
        tap(d, DIAL_CW)        # e
        tap(d, DIAL_ENTER)     # commit e
        tap(d, DIAL_CW)        # re-shows e (no move)
        tap(d, DIAL_ENTER)
        self.assertEqual(screen(d.out.log), "ee")

    def test_wraps_backwards_to_z(self):
        d = make()
        tap(d, DIAL_CW)
        tap(d, DIAL_CCW)
        self.assertEqual(screen(d.out.log), "z")

    def test_set_switch(self):
        d = make()
        dpad(d, "down")        # ABC
        self.assertEqual(screen(d.out.log), "E")
        dpad(d, "down")        # 123
        dpad(d, "down")        # symbols
        self.assertEqual(screen(d.out.log), "/")

    def test_space_commits_preview(self):
        d = make()
        tap(d, DIAL_CW)
        tap(d, CROSS)
        tap(d, DIAL_CW)
        self.assertEqual(screen(d.out.log), "e e")

    def test_backspace_cancels_preview_then_deletes(self):
        d = make()
        tap(d, DIAL_CW); tap(d, DIAL_ENTER)
        tap(d, DIAL_CW)
        tap(d, CIRCLE)
        self.assertEqual(screen(d.out.log), "e")
        tap(d, CIRCLE)
        self.assertEqual(screen(d.out.log), "")

    def type_re(self, d):
        # r is index 8 in "etaoinshrdl...": one press shows e, eight more reach r.
        for _ in range(9):
            tap(d, DIAL_CW)
        tap(d, DIAL_ENTER)
        # First press re-shows r, eight more step back to e.
        for _ in range(9):
            tap(d, DIAL_CCW)
        tap(d, DIAL_ENTER)

    def test_suggestions_cycle_and_accept(self):
        d = make()
        self.type_re(d)
        self.assertEqual(screen(d.out.log), "re")
        tap(d, SQUARE)
        self.assertEqual(screen(d.out.log), "refactor")
        tap(d, SQUARE)
        self.assertEqual(screen(d.out.log), "readme")
        tap(d, TRIANGLE)
        self.assertEqual(screen(d.out.log), "readme ")

    def test_triangle_without_visible_suggestion_inserts_top(self):
        d = make()
        self.type_re(d)
        tap(d, TRIANGLE)
        self.assertEqual(screen(d.out.log), "refactor ")

    def test_backspace_removes_suggestion(self):
        d = make()
        self.type_re(d)
        tap(d, SQUARE)
        tap(d, CIRCLE)
        self.assertEqual(screen(d.out.log), "re")

    def test_gas_still_submits(self):
        d = make()
        tap(d, DIAL_CW)
        for v in (255, 0, 255):
            axis(d, "ABS_Z", v)
        self.assertEqual(d.out.log[-1], ("key", "enter"))
        self.assertEqual(screen(d.out.log), "e")

    def test_paddles_fall_through_to_drive(self):
        d = make()
        tap(d, 292)
        self.assertEqual(d.out.log, [("key", "down")])

    def test_share_returns_to_drive(self):
        d = make()
        tap(d, 296)
        tap(d, CROSS)
        self.assertEqual(d.out.log, [("key", "enter")])


class WordModelTest(unittest.TestCase):
    def test_keeps_typed_case(self):
        m = FixedModel(["refactor"])
        self.assertEqual(m.suggest("Ref"), ["Refactor"])

    def test_base_list_loads(self):
        m = WordModel()
        self.assertIn("commit", m.suggest("comm"))


if __name__ == "__main__":
    unittest.main()

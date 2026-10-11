import asyncio
import json
import os
import tempfile
import unittest
from collections import Counter, defaultdict

from pathlib import Path

from g923 import config
from g923.daemon import Daemon

REPO_CONFIG = Path(__file__).resolve().parent.parent / "config.toml"
from g923.typing import WordModel
from tests.test_drive import Recorder, SilentFeedback, axis, key, tap


class FixedModel(WordModel):
    def __init__(self, words, follows=None):
        self.scores = Counter({w: len(words) - i for i, w in enumerate(words)})
        self.follows = defaultdict(Counter, {a: Counter(b) for a, b in (follows or {}).items()})
        self.starts = Counter()


def make(words=("refactor", "readme", "repo", "reply"), follows=None):
    from g923.typing import Typer

    cfg = config.load(REPO_CONFIG)
    out, fb = Recorder(), SilentFeedback()
    d = Daemon(cfg, out, fb, typer=Typer(cfg, out, fb, FixedModel(list(words), follows)))
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
PADDLE_RIGHT, PADDLE_LEFT = 292, 293


def fast_backspace(d, after=0.05, rate=0.05, word_after=1.0, word_rate=0.05):
    """Shrink the hold timings so the repeat tests run quickly."""
    t = d.cfg["type"]
    t["backspace_repeat_after_s"] = after
    t["backspace_repeat_rate_s"] = rate
    t["backspace_word_after_s"] = word_after
    t["backspace_word_rate_s"] = word_rate


def hold(d, code, seconds):
    """Press a button, keep it down for `seconds`, then release it."""
    async def go():
        key(d, code, True)
        await asyncio.sleep(seconds)
        key(d, code, False)
        await asyncio.sleep(0.02)  # let the cancelled repeat task wind down
    asyncio.run(go())


def sent(d, combo):
    return d.out.log.count(("key", combo))


class TypeModeTest(unittest.TestCase):
    def test_first_press_previews_a(self):
        d = make()
        dpad(d, "right")
        self.assertEqual(screen(d.out.log), "a")

    def test_scrolling_replaces_preview(self):
        d = make()
        for _ in range(3):
            tap(d, DIAL_CW)  # a, b, c
        self.assertEqual(screen(d.out.log), "c")
        dpad(d, "left")
        self.assertEqual(screen(d.out.log), "b")

    def test_commit_and_double_letter(self):
        d = make()
        tap(d, DIAL_CW)        # a
        tap(d, DIAL_ENTER)     # commit a
        tap(d, DIAL_CW)        # re-shows a (no move)
        tap(d, DIAL_ENTER)
        self.assertEqual(screen(d.out.log), "aa")

    def test_wraps_backwards_to_z(self):
        d = make()
        tap(d, DIAL_CW)
        tap(d, DIAL_CCW)
        self.assertEqual(screen(d.out.log), "z")

    def test_set_switch(self):
        d = make()
        dpad(d, "down")        # ABC
        self.assertEqual(screen(d.out.log), "A")
        dpad(d, "down")        # 123
        dpad(d, "down")        # symbols
        self.assertEqual(screen(d.out.log), "/")

    def test_space_commits_preview(self):
        d = make()
        tap(d, DIAL_CW)
        tap(d, CROSS)
        tap(d, DIAL_CW)
        self.assertEqual(screen(d.out.log), "a a")

    def test_backspace_cancels_preview_then_deletes(self):
        d = make()
        tap(d, DIAL_CW); tap(d, DIAL_ENTER)
        tap(d, DIAL_CW)
        tap(d, CIRCLE)
        self.assertEqual(screen(d.out.log), "a")
        tap(d, CIRCLE)
        self.assertEqual(screen(d.out.log), "")

    def type_re(self, d):
        letters = d.typer.sets[0]
        r, e = letters.index("r"), letters.index("e")
        # One press shows the current letter, then each press moves one.
        for _ in range(r + 1):
            tap(d, DIAL_CW)
        tap(d, DIAL_ENTER)
        for _ in range(r - e + 1):
            tap(d, DIAL_CCW)
        tap(d, DIAL_ENTER)

    def test_triangle_picks_the_best_word(self):
        d = make()
        self.type_re(d)
        self.assertEqual(d.typer.suggestions(), {"left": "readme", "center": "refactor", "right": "repo"})
        tap(d, TRIANGLE)
        self.assertEqual(screen(d.out.log), "refactor ")

    def test_paddles_pick_the_side_words(self):
        d = make()
        self.type_re(d)
        tap(d, PADDLE_LEFT)
        self.assertEqual(screen(d.out.log), "readme ")
        d = make()
        self.type_re(d)
        tap(d, PADDLE_RIGHT)
        self.assertEqual(screen(d.out.log), "repo ")

    def test_square_offers_three_more(self):
        d = make()
        self.type_re(d)
        tap(d, SQUARE)
        self.assertEqual(d.typer.suggestions()["center"], "reply")
        tap(d, TRIANGLE)
        self.assertEqual(screen(d.out.log), "reply ")

    def test_the_letter_you_are_on_counts(self):
        d = make()
        for _ in range(d.typer.sets[0].index("r") + 1):
            tap(d, DIAL_CW)                     # "r" showing, not committed
        self.assertEqual(d.typer.suggestions()["center"], "refactor")
        tap(d, TRIANGLE)
        self.assertEqual(screen(d.out.log), "refactor ")

    def test_next_word_after_a_pick(self):
        d = make(words=["fix", "it"], follows={"fix": {"the": 5, "it": 2, "a": 1}})
        tap(d, DIAL_CW)                         # preview "a"
        tap(d, CIRCLE)                          # gone again
        d.typer.prev = None
        for _ in range(d.typer.sets[0].index("f") + 1):
            tap(d, DIAL_CW)
        tap(d, TRIANGLE)                        # "fix "
        self.assertEqual(d.typer.suggestions(), {"left": "it", "center": "the", "right": "a"})
        tap(d, TRIANGLE)
        self.assertEqual(screen(d.out.log), "fix the ")

    def test_no_suggestions_after_punctuation(self):
        d = make()
        tap(d, 709)
        dpad(d, "down"); dpad(d, "down"); dpad(d, "down")   # symbols set
        self.assertIsNone(d.typer.prefix())
        self.assertEqual(d.typer.suggestions(), {"left": None, "center": None, "right": None})
        before = list(d.out.log)
        tap(d, TRIANGLE)
        self.assertEqual(d.out.log, before)   # nothing typed

    def test_held_backspace_repeats(self):
        d = make()
        fast_backspace(d)
        for _ in range(4):
            tap(d, DIAL_CW); tap(d, DIAL_ENTER)   # aaaa
        hold(d, CIRCLE, 0.3)
        self.assertGreaterEqual(sent(d, "backspace"), 3)  # one on press + repeats
        self.assertEqual(screen(d.out.log), "")
        self.assertEqual(sent(d, "ctrl+w"), 0)  # word timeout not reached

    def test_backspace_stops_when_released(self):
        d = make()
        fast_backspace(d)
        for _ in range(4):
            tap(d, DIAL_CW); tap(d, DIAL_ENTER)
        hold(d, CIRCLE, 0.3)
        sent_so_far = sent(d, "backspace")

        async def idle():
            await asyncio.sleep(0.2)

        asyncio.run(idle())
        self.assertEqual(sent(d, "backspace"), sent_so_far)

    def test_other_type_buttons_stop_the_repeat(self):
        d = make()
        fast_backspace(d)
        for _ in range(3):
            tap(d, DIAL_CW); tap(d, DIAL_ENTER)   # aaa

        async def go():
            key(d, CIRCLE, True)
            await asyncio.sleep(0.03)             # first delete, no repeat yet
            tap(d, DIAL_CW)                       # preview a while ○ is still down
            await asyncio.sleep(0.2)
            key(d, CIRCLE, False)
            await asyncio.sleep(0.02)

        asyncio.run(go())
        self.assertEqual(screen(d.out.log), "aaa")  # two kept, plus the new preview
        self.assertEqual(d.typer.preview, "a")

    def test_hold_switches_to_deleting_words(self):
        d = make()
        fast_backspace(d, word_after=0.15)
        for _ in range(4):
            tap(d, DIAL_CW); tap(d, DIAL_ENTER)
        hold(d, CIRCLE, 0.4)
        self.assertGreater(sent(d, "ctrl+w"), 0)
        # Character backspaces come first, Ctrl+W only after the timeout.
        self.assertLess(d.out.log.index(("key", "backspace")),
                        d.out.log.index(("key", "ctrl+w")))
        self.assertEqual(d.typer.word, "")

    def test_word_deletes_repeat_at_their_own_rate(self):
        d = make()
        fast_backspace(d, rate=0.02, word_after=0.1, word_rate=0.3)
        for _ in range(4):
            tap(d, DIAL_CW); tap(d, DIAL_ENTER)
        hold(d, CIRCLE, 0.5)
        # About 0.4 s of word mode at one per 0.3 s: two at most, not twenty.
        self.assertIn(sent(d, "ctrl+w"), (1, 2))

    def test_backspace_word_deletes_the_whole_word(self):
        d = make()
        tap(d, DIAL_CW); tap(d, DIAL_ENTER)   # commit a
        self.assertEqual(d.typer.word, "a")
        d.typer.backspace_word()
        self.assertEqual(d.out.log, [("type", "a"), ("key", "ctrl+w")])
        self.assertEqual(d.typer.word, "")

    def test_backspace_word_clears_a_previewed_character(self):
        d = make()
        dpad(d, "right")                      # preview a
        d.typer.backspace_word()
        self.assertEqual(screen(d.out.log), "")
        self.assertIsNone(d.typer.preview)
        self.assertEqual(sent(d, "ctrl+w"), 0)

    def test_backspace_word_forgets_the_context(self):
        d = make(follows={"fix": {"the": 1}})
        d.typer.prev = "fix"
        d.typer.backspace_word()
        self.assertIsNone(d.typer.prev)

    def test_gas_still_submits(self):
        d = make()
        tap(d, DIAL_CW)
        for v in (255, 0, 255):
            axis(d, "ABS_Z", v)
        self.assertEqual(d.out.log[-1], ("key", "enter"))
        self.assertEqual(screen(d.out.log), "a")

    def test_paddles_pick_suggestions_in_type_mode(self):
        d = make()
        tap(d, PADDLE_RIGHT)
        self.assertNotIn(("key", "down"), d.out.log)

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

    def test_built_in_phrases_predict_the_next_word(self):
        m = WordModel()
        self.assertIn("the", m.suggest("", 3, prev="fix"))
        self.assertIn("you", m.suggest("", 3, prev="can"))

    def test_learns_from_history_but_skips_placeholders_and_commands(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "history.jsonl")
            with open(path, "w") as f:
                for text in ["deploy the staging server", "deploy the staging server",
                             "[Pasted text #1 +40 lines] zebra", "/model opus"]:
                    f.write(json.dumps({"display": text}) + "\n")
            m = WordModel(path)
        self.assertEqual(m.suggest("", 1, prev="the"), ["staging"])
        self.assertNotIn("pasted", m.follows)
        self.assertNotIn("opus", m.scores)

    def test_context_beats_plain_frequency(self):
        m = FixedModel(["readme", "refactor"], follows={"the": {"refactor": 1}})
        self.assertEqual(m.suggest("re", 2, prev="the"), ["refactor", "readme"])
        self.assertEqual(m.suggest("re", 2), ["readme", "refactor"])


if __name__ == "__main__":
    unittest.main()

"""Type mode: pick characters with the D-pad/dial and commit with dial Enter.

The character being chosen is typed into the prompt as a live preview and
replaced (Backspace + next character) as you scroll. Suggested words are typed
inline too. What you see is what you get: visible text stays unless you remove
it with Backspace.
"""

import json
import re
from collections import Counter
from pathlib import Path

from . import config

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]{2,}")
SET_NAMES = ["abc", "ABC", "123", "symbols"]


class WordModel:
    def __init__(self, history_file=None):
        self.scores = Counter()
        base = Path(__file__).with_name("words.txt").read_text().split("\n")
        words = [w for line in base if not line.startswith("#") for w in line.split()]
        for rank, w in enumerate(words):
            self.scores[w.lower()] += max(1, 50 - rank // 10)
        if history_file:
            self.learn(config.expand(history_file))

    def learn(self, path):
        """Blend in words from past Claude Code prompts (read locally, never stored)."""
        try:
            with open(path) as f:
                for line in f:
                    try:
                        text = json.loads(line).get("display", "")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    for w in WORD_RE.findall(text):
                        self.scores[w.lower()] += 3
        except OSError:
            pass

    def suggest(self, prefix, limit=5):
        p = prefix.lower()
        hits = [w for w in self.scores if w.startswith(p) and len(w) > len(p)]
        hits.sort(key=lambda w: -self.scores[w])
        # Keep the prefix exactly as typed, so "Ref" suggests "Refactor".
        return [prefix + w[len(p):] for w in hits[:limit]]


class Typer:
    def __init__(self, cfg, out, fb, model=None):
        t = cfg["type"]
        self.sets = t["sets"]
        self.suggest_after = t["suggest_after"]
        self.out = out
        self.fb = fb
        self.model = model or WordModel(t.get("history_file"))
        self.set_idx = 0
        self.char_idx = 0
        self.preview = None    # character currently shown but not committed
        self.word = ""         # committed characters of the current word
        self.suggestion = None  # (candidates, index, remainder shown)

    @property
    def charset(self):
        return self.sets[self.set_idx]

    # --- characters -----------------------------------------------------

    def step(self, direction):
        """Show the previous/next character. The first press after a commit
        re-shows the current character, so double letters are one press."""
        self._settle_suggestion()
        if self.preview is not None:
            self.out.press("backspace")
            self.char_idx = (self.char_idx + direction) % len(self.charset)
        self._show(self.charset[self.char_idx])

    def change_set(self, direction):
        self._settle_suggestion()
        self.set_idx = (self.set_idx + direction) % len(self.sets)
        self.char_idx = 0
        self.fb.notify("Type", SET_NAMES[self.set_idx] if self.set_idx < len(SET_NAMES) else f"set {self.set_idx + 1}")
        if self.preview is not None:
            self.out.press("backspace")
        self._show(self.charset[0])

    def commit(self):
        if self.preview is None:
            return
        ch, self.preview = self.preview, None
        self.word = self.word + ch if (ch.isalnum() or ch in "_-'") else ""
        if len(self.word) >= self.suggest_after:
            hints = self.model.suggest(self.word, 3)
            if hints:
                self.fb.notify("□ suggest", "  ·  ".join(hints))

    def space(self):
        self._settle()
        self.out.type(" ")
        self.word = ""

    def backspace(self):
        if self.suggestion:
            self._erase(len(self.suggestion[2]))
            self.suggestion = None
        elif self.preview is not None:
            self._erase(1)
            self.preview = None
        else:
            self._erase(1)
            self.word = self.word[:-1]

    # --- word suggestions -----------------------------------------------

    def next_suggestion(self):
        if self.preview is not None:
            self.commit()
        if self.suggestion:
            cands, idx, shown = self.suggestion
            self._erase(len(shown))
            idx = (idx + 1) % len(cands)
        else:
            cands = self.model.suggest(self.word) if len(self.word) >= self.suggest_after else []
            if not cands:
                self.fb.notify("Type", "no suggestions")
                return
            idx = 0
        rest = cands[idx][len(self.word):]
        self.out.type(rest)
        self.suggestion = (cands, idx, rest)

    def accept(self):
        if not self.suggestion:
            self.next_suggestion()
            if not self.suggestion:
                return
        self.suggestion = None
        self.out.type(" ")
        self.word = ""

    # --- helpers --------------------------------------------------------

    def finalize(self):
        """Leave whatever is visible in place and reset state."""
        self.preview = None
        self.suggestion = None
        self.word = ""

    def _show(self, ch):
        self.out.type(ch)
        self.preview = ch

    def _erase(self, n):
        for _ in range(n):
            self.out.press("backspace")

    def _settle_suggestion(self):
        # A visible suggestion becomes part of the word once you move on.
        if self.suggestion:
            self.word += self.suggestion[2]
            self.suggestion = None

    def _settle(self):
        if self.preview is not None:
            self.commit()
        self._settle_suggestion()

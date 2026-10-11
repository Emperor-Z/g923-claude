"""Type mode: pick characters with the D-pad/dial and commit with dial Enter.

The character being chosen is typed into the prompt as a live preview and
replaced (Backspace + next character) as you scroll. What you see is what you
get: visible text stays unless you remove it with Backspace.

Like a phone keyboard, three word suggestions are always on offer: endings
for the word you're typing (counting the letter you're on), or the likely
next word once a word is done. Left paddle, △ and right paddle pick the left,
middle (best) and right one; □ shows three more.
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from . import config

WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]{2,}")    # worth completing
TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_'-]*")      # any word, for what follows what
SENTENCE_RE = re.compile(r"[.!?]+(?:\s|$)|\n")   # not the dot in "context.md"
PLACEHOLDER_RE = re.compile(r"\[(?:Pasted text|Image)[^\]]*\]")
WORD_CHARS = "_-'"
SET_NAMES = ["abc", "ABC", "123", "symbols"]
SLOTS = ("left", "center", "right")


def is_word_char(ch):
    return ch.isalnum() or ch in WORD_CHARS


class WordModel:
    def __init__(self, history_file=None):
        self.scores = Counter()                  # how common each word is
        self.follows = defaultdict(Counter)      # word -> the words that come after it
        self.starts = Counter()                  # how prompts begin
        here = Path(__file__).parent
        base = (here / "words.txt").read_text().split("\n")
        words = [w for line in base if not line.startswith("#") for w in line.split()]
        for rank, w in enumerate(words):
            self.scores[w.lower()] += max(1, 50 - rank // 10)
        for line in (here / "phrases.txt").read_text().split("\n"):
            if line and not line.startswith("#"):
                self.learn_text(line, weight=1)
        if history_file:
            self.learn(config.expand(history_file))

    def learn(self, path):
        """Blend in past Claude Code prompts (read locally, never stored)."""
        try:
            with open(path) as f:
                for line in f:
                    try:
                        text = json.loads(line).get("display", "")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if isinstance(text, str) and not text.startswith("/"):  # skip slash commands
                        self.learn_text(text, weight=3)
        except OSError:
            pass

    def learn_text(self, text, weight):
        text = PLACEHOLDER_RE.sub(" ", text)
        for w in WORD_RE.findall(text):
            self.scores[w.lower()] += weight
        for sentence in SENTENCE_RE.split(text):
            words = [w.lower() for w in TOKEN_RE.findall(sentence)]
            if words:
                self.starts[words[0]] += weight
            for a, b in zip(words, words[1:]):
                self.follows[a][b] += weight

    def suggest(self, prefix, limit=5, prev=None):
        """Words for the next slot: endings for `prefix` (or, with no prefix, the
        likely next word), best first. `prev` is the word before, if known."""
        p = prefix.lower()
        after = self.follows.get(prev, Counter()) if prev else self.starts
        ranked = [w for w, _ in after.most_common() if w.startswith(p) and len(w) > len(p)]
        if p:  # then the commonest words that start the same way
            common = sorted((w for w in self.scores if w.startswith(p) and len(w) > len(p)),
                            key=lambda w: -self.scores[w])
            ranked += [w for w in common if w not in ranked]
        elif not prev:
            ranked += [w for w, _ in self.scores.most_common(limit) if w not in ranked]
        # Keep the prefix exactly as typed, so "Ref" suggests "Refactor".
        return [prefix + w[len(p):] for w in ranked[:limit]]


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
        self.prev = None       # the word before this one, for next-word suggestions
        self.page = 0          # which three suggestions are on offer (□ shows more)

    @property
    def charset(self):
        return self.sets[self.set_idx]

    # --- characters -----------------------------------------------------

    def step(self, direction):
        """Show the previous/next character. The first press after a commit
        re-shows the current character, so double letters are one press."""
        self.page = 0
        if self.preview is not None:
            self.out.press("backspace")
            self.char_idx = (self.char_idx + direction) % len(self.charset)
        self._show(self.charset[self.char_idx])

    def change_set(self, direction):
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
        self.page = 0
        if is_word_char(ch):
            self.word += ch
        else:
            if self.word:
                self.prev = self.word.lower()
            self.word = ""
            if ch in ".!?":
                self.prev = None   # a new sentence
        if len(self.word) >= self.suggest_after:
            self.announce()

    def space(self):
        if self.preview is not None:
            self.commit()
        self.out.type(" ")
        if self.word:
            self.prev = self.word.lower()
        self.word = ""
        self.page = 0

    def backspace(self):
        self.page = 0
        if self.preview is not None:
            self._erase(1)
            self.preview = None
        else:
            self._erase(1)
            if not self.word:
                self.prev = None   # deleted past the word we knew about
            self.word = self.word[:-1]

    def backspace_word(self):
        """Delete the whole word before the cursor (Ctrl+W) and forget it."""
        self.page = 0
        if self.preview is not None:
            self._erase(1)
            self.preview = None
        else:
            self.out.press("ctrl+w")
            self.word = ""
            self.prev = None

    # --- word suggestions -----------------------------------------------

    def prefix(self):
        """What's typed of the current word, counting a visible preview letter,
        or None when the cursor isn't in a word (say, after a comma)."""
        if self.preview is None:
            return self.word
        return self.word + self.preview if is_word_char(self.preview) else None

    def suggestions(self):
        """The three on offer, as {left, center, right}; the best is in the middle."""
        prefix = self.prefix()
        if prefix is None:
            return dict.fromkeys(SLOTS)
        found = self.model.suggest(prefix, 9, prev=self.prev)
        page = found[self.page * 3:self.page * 3 + 3] or found[:3]
        page += [None] * (3 - len(page))
        return {"left": page[1], "center": page[0], "right": page[2]}

    def pick(self, slot):
        """Type the rest of the suggested word in `slot`, then a space."""
        word = self.suggestions()[slot]
        if not word:
            self.fb.notify("Type", "no suggestion there")
            return
        typed = self.prefix() or ""
        self.out.type(word[len(typed):] + " ")
        self.preview = None   # it's now part of the word, as typed
        self.word = ""
        self.prev = word.lower()
        self.page = 0

    def more(self):
        """Offer the next three suggestions."""
        self.page = (self.page + 1) % 3
        self.announce()

    def announce(self):
        """Show the suggestions in a notification, for when the HUD isn't running."""
        s = self.suggestions()
        if s["center"]:
            self.fb.notify("Suggestions", f"◀ {s['left'] or '—'}   △ {s['center']}   {s['right'] or '—'} ▶")

    # --- helpers --------------------------------------------------------

    def finalize(self):
        """Leave whatever is visible in place and reset state."""
        self.preview = None
        self.word = ""
        self.prev = None
        self.page = 0

    def _show(self, ch):
        self.out.type(ch)
        self.preview = ch

    def _erase(self, n):
        for _ in range(n):
            self.out.press("backspace")

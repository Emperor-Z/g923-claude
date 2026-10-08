"""Virtual keyboard and mouse.

Actions are xdotool-style combos ("enter", "shift+tab", "ctrl+o"), plus
"hold:<key>" for keys that mirror a button's press and release. Text is typed
assuming a US layout.
"""

import time

from evdev import UInput
from evdev import ecodes as e

ALIASES = {
    "enter": "KEY_ENTER", "return": "KEY_ENTER", "esc": "KEY_ESC",
    "tab": "KEY_TAB", "space": "KEY_SPACE", "backspace": "KEY_BACKSPACE",
    "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT", "right": "KEY_RIGHT",
    "ctrl": "KEY_LEFTCTRL", "shift": "KEY_LEFTSHIFT", "alt": "KEY_LEFTALT",
    "pageup": "KEY_PAGEUP", "pagedown": "KEY_PAGEDOWN",
}

# US layout: unshifted and shifted symbol keys.
SYMBOLS = {
    "-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE",
    "\\": "BACKSLASH", ";": "SEMICOLON", "'": "APOSTROPHE", "`": "GRAVE",
    ",": "COMMA", ".": "DOT", "/": "SLASH", " ": "SPACE", "\n": "ENTER",
}
SHIFTED = {
    "!": "1", "@": "2", "#": "3", "$": "4", "%": "5", "^": "6", "&": "7",
    "*": "8", "(": "9", ")": "0", "_": "-", "+": "=", "{": "[", "}": "]",
    "|": "\\", ":": ";", '"': "'", "~": "`", "<": ",", ">": ".", "?": "/",
}


def keycode(name):
    name = name.strip().lower()
    full = ALIASES.get(name) or f"KEY_{name.upper()}"
    if full not in e.ecodes:
        raise ValueError(f"unknown key: {name}")
    return e.ecodes[full]


def parse_combo(combo):
    return [keycode(part) for part in combo.split("+")]


def char_keys(ch):
    """Return (keycode, needs_shift) for one character."""
    if ch in SHIFTED:
        return char_keys(SHIFTED[ch])[0], True
    if ch.isalpha():
        return e.ecodes[f"KEY_{ch.upper()}"], ch.isupper()
    if ch.isdigit():
        return e.ecodes[f"KEY_{ch}"], False
    if ch in SYMBOLS:
        return e.ecodes[f"KEY_{SYMBOLS[ch]}"], False
    raise ValueError(f"can't type {ch!r}")


class Injector:
    """Sends keys and scroll events through uinput."""

    KEY_DELAY = 0.008

    def __init__(self):
        keys = [c for n, c in e.ecodes.items() if n.startswith("KEY_") and c < 0x200]
        self.kbd = UInput({e.EV_KEY: keys}, name="g923-claude keyboard")
        # libinput only treats a device as a mouse if it has motion + buttons.
        self.mouse = UInput(
            {
                e.EV_KEY: [e.BTN_LEFT, e.BTN_RIGHT],
                e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL],
            },
            name="g923-claude mouse",
        )
        time.sleep(0.3)  # let the compositor pick up the new devices

    def _emit(self, code, value):
        self.kbd.write(e.EV_KEY, code, value)
        self.kbd.syn()
        time.sleep(self.KEY_DELAY)

    def press(self, combo):
        codes = parse_combo(combo)
        for c in codes:
            self._emit(c, 1)
        for c in reversed(codes):
            self._emit(c, 0)

    def hold(self, key, down):
        self._emit(keycode(key), 1 if down else 0)

    def type(self, text):
        for ch in text:
            code, shift = char_keys(ch)
            if shift:
                self._emit(e.KEY_LEFTSHIFT, 1)
            self._emit(code, 1)
            self._emit(code, 0)
            if shift:
                self._emit(e.KEY_LEFTSHIFT, 0)

    def scroll(self, notches):
        self.mouse.write(e.EV_REL, e.REL_WHEEL, notches)
        self.mouse.syn()

    def close(self):
        self.kbd.close()
        self.mouse.close()


class DryRun:
    """Prints what would be sent instead of sending it."""

    def press(self, combo):
        parse_combo(combo)
        print(f"  key   {combo}", flush=True)

    def hold(self, key, down):
        print(f"  hold  {key} {'down' if down else 'up'}", flush=True)

    def type(self, text):
        for ch in text:
            char_keys(ch)
        print(f"  type  {text!r}", flush=True)

    def scroll(self, notches):
        print(f"  scroll {notches:+d}", flush=True)

    def close(self):
        pass

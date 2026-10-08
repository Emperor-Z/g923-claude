"""Desktop and wheel feedback.

Notifications go through notify-send and sounds through canberra-gtk-play.
Force feedback is added in phase 5 via `attach_wheel`; until then each event
falls back to sound.
"""

import shutil
import subprocess

SOUNDS = {
    "done": "complete",
    "attention": "dialog-warning",
    "grind": "dialog-error",
    "arm": "service-login",
    "disarm": "service-logout",
}


class Feedback:
    def __init__(self, cfg):
        self.cfg = cfg["feedback"]
        self.has_notify = shutil.which("notify-send") is not None
        self.has_sound = shutil.which("canberra-gtk-play") is not None

    def notify(self, title, body=""):
        print(f"[notify] {title} {body}".rstrip(), flush=True)
        if self.cfg.get("notify") and self.has_notify:
            self._spawn(["notify-send", "-a", "g923-claude", "-t", "2000",
                         "-h", "string:x-canonical-private-synchronous:g923", title, body])

    def sound(self, event):
        if self.cfg.get("sound") and self.has_sound and event in SOUNDS:
            self._spawn(["canberra-gtk-play", "-i", SOUNDS[event]])

    # --- events -----------------------------------------------------------

    def done(self):
        self.sound("done")

    def attention(self):
        self.sound("attention")

    def grind(self):
        self.sound("grind")
        self.notify("Grind!", "Hold the clutch to change gear")

    def armed(self, armed):
        self.sound("arm" if armed else "disarm")

    def _spawn(self, argv):
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

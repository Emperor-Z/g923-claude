"""Desktop and wheel feedback.

Notifications go through notify-send and sounds through canberra-gtk-play.
When the wheel exposes force feedback, events are felt on the wheel instead of
heard; otherwise they fall back to sound.
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
        self.wheel = None

    def attach_wheel(self, dev, ffb_cfg):
        from .ffb import Wheel

        self.detach_wheel()
        if ffb_cfg.get("enabled", True):
            self.wheel = Wheel(dev, ffb_cfg)

    def detach_wheel(self):
        if self.wheel:
            self.wheel.close()
        self.wheel = None

    def _feel(self, event):
        """Play a wheel effect, or the matching sound if there's no FFB."""
        if not (self.wheel and self.wheel.play(event)):
            self.sound(event)

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
        self._feel("done")

    def attention(self):
        self._feel("attention")

    def grind(self):
        self._feel("grind")
        self.notify("Grind!", "Hold the clutch to change gear")

    def armed(self, armed):
        self._feel("arm" if armed else "disarm")

    def _spawn(self, argv):
        try:
            subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            pass

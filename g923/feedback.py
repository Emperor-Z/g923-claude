import shutil
import subprocess


class Feedback:
    def __init__(self, cfg):
        self.cfg = cfg["feedback"]
        self.has_notify = shutil.which("notify-send") is not None

    def notify(self, title, body=""):
        print(f"[notify] {title} {body}".rstrip(), flush=True)
        if self.cfg.get("notify") and self.has_notify:
            subprocess.Popen(
                ["notify-send", "-a", "g923-claude", "-t", "2000",
                 "-h", "string:x-canonical-private-synchronous:g923", title, body],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )

    def grind(self):
        self.notify("Grind!", "Hold the clutch to change gear")

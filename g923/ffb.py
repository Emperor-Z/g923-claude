"""Force-feedback effects on the wheel.

Effects are uploaded once when the wheel connects and replayed by id, which
keeps us well inside the device's effect slots. Every call is best-effort:
a wheel without force feedback, or one that disappears, is silently skipped.
"""

from evdev import ecodes as e
from evdev import ff

DIRECTION = 0x4000  # along the steering axis


def constant(level, length_ms, delay_ms=0):
    return ff.Effect(
        e.FF_CONSTANT, -1, DIRECTION, ff.Trigger(0, 0), ff.Replay(length_ms, delay_ms),
        ff.EffectType(ff_constant_effect=ff.Constant(level, ff.Envelope(0, 0, 0, 0))),
    )


def periodic(waveform, period_ms, magnitude, length_ms, fade_ms=0):
    return ff.Effect(
        e.FF_PERIODIC, -1, DIRECTION, ff.Trigger(0, 0), ff.Replay(length_ms, 0),
        ff.EffectType(ff_periodic_effect=ff.Periodic(
            waveform, period_ms, magnitude, 0, 0,
            ff.Envelope(0, 0, fade_ms, 0), 0, None)),
    )


# name -> list of effects played together (delays inside sequence them)
EFFECTS = {
    # Claude finished: two quick kicks, left then right.
    "done": [constant(0x5000, 70), constant(-0x5000, 70, delay_ms=140)],
    # Permission waiting: slow side-to-side wiggle.
    "attention": [periodic(e.FF_SINE, 260, 0x3800, 1040, fade_ms=200)],
    # Shifted without the clutch: harsh buzz.
    "grind": [periodic(e.FF_SQUARE, 28, 0x3000, 260)],
    # Armed / disarmed: single short tap.
    "arm": [constant(0x4000, 60)],
    "disarm": [constant(-0x3000, 50), constant(-0x3000, 50, delay_ms=110)],
}


class Wheel:
    def __init__(self, dev, cfg):
        self.dev = dev
        self.ids = {}
        caps = dev.capabilities().get(e.EV_FF, [])
        if not caps:
            print("wheel has no force feedback (driver?); using sound only", flush=True)
            return
        try:
            self._set(e.FF_GAIN, cfg.get("strength", 0.6))
            if e.FF_AUTOCENTER in caps:
                self._set(e.FF_AUTOCENTER, cfg.get("autocenter", 0.15))
            for name, effects in EFFECTS.items():
                self.ids[name] = [dev.upload_effect(fx) for fx in effects]
            print(f"force feedback ready ({len(self.ids)} effects)", flush=True)
        except OSError as err:
            print(f"force feedback unavailable: {err}", flush=True)
            self.ids = {}

    @property
    def ok(self):
        return bool(self.ids)

    def play(self, name):
        try:
            for eid in self.ids.get(name, []):
                self.dev.write(e.EV_FF, eid, 1)
            return name in self.ids
        except OSError:
            self.ids = {}
            return False

    def close(self):
        for ids in self.ids.values():
            for eid in ids:
                try:
                    self.dev.erase_effect(eid)
                except OSError:
                    pass
        self.ids = {}

    def _set(self, code, fraction):
        self.dev.write(e.EV_FF, code, int(max(0.0, min(1.0, fraction)) * 0xFFFF))

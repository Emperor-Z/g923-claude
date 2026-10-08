import argparse
import asyncio
import sys
import time

import evdev
from evdev import ecodes as e

from . import config
from .feedback import Feedback
from .keys import DryRun, Injector
from .typing import Typer

HATS = {
    ("ABS_HAT0Y", -1): "dpad_up", ("ABS_HAT0Y", 1): "dpad_down",
    ("ABS_HAT0X", -1): "dpad_left", ("ABS_HAT0X", 1): "dpad_right",
}


def find_device(name_match):
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if name_match in dev.name:
            return dev
        dev.close()
    return None


class Pedal:
    """Turns an inverted 0-255 pedal axis into press/release edges."""

    def __init__(self, press_at, release_at):
        self.press_at = press_at
        self.release_at = release_at
        # Start latched so a bogus first reading can't fire; the first real
        # report (pedal at rest) releases it.
        self.down = True

    def update(self, value):
        amount = (255 - value) / 255
        if not self.down and amount >= self.press_at:
            self.down = True
            return "press"
        if self.down and amount <= self.release_at:
            self.down = False
            return "release"
        return None


class Daemon:
    def __init__(self, cfg, out, fb, typer=None):
        self.cfg = cfg
        self.out = out
        self.fb = fb
        self.typer = typer or Typer(cfg, out, fb)
        self.armed = cfg["modes"].get("start_armed", False)
        self.mode = "drive"
        p = cfg["pedals"]
        self.pedal_axes = {p["gas"]: "gas", p["brake"]: "brake", p["clutch"]: "clutch"}
        self.pedals = {n: Pedal(p["press_at"], p["release_at"]) for n in self.pedal_axes.values()}
        self.clutch_down_at = None
        self.clutch_used = False
        s = cfg["steering"]
        self.steer_axis = s["axis"]
        self.steer_value = s["center"]  # absinfo reads 0 until the first report
        self.scroll_acc = 0.0
        self.held = set()
        eff = cfg["effort"]
        self.effort = eff["levels"].index(eff["start"])

    # --- input dispatch -------------------------------------------------

    def handle(self, ev):
        if ev.type == e.EV_KEY:
            name = self.cfg["buttons"].get(ev.code)
            if name:
                self.button(name, ev.value == 1)
        elif ev.type == e.EV_ABS:
            axis = e.ABS.get(ev.code)
            if axis == self.steer_axis:
                self.steer_value = ev.value
            elif axis in self.pedal_axes:
                name = self.pedal_axes[axis]
                edge = self.pedals[name].update(ev.value)
                if edge:
                    self.pedal(name, edge == "press")
            elif axis and axis.startswith("ABS_HAT"):
                for direction in (-1, 1):
                    self.button(HATS[(axis, direction)], ev.value == direction)

    def button(self, name, down):
        if name == self.cfg["modes"]["arm_toggle"]:
            if down:
                self.set_armed(not self.armed)
            return
        if not self.armed:
            return
        modes = self.cfg["modes"]
        if name in (modes["drive"], modes["type"]):
            if down:
                self.set_mode("drive" if name == modes["drive"] else "type")
            return
        if self.mode == "type" and name in self.cfg["type_keys"]:
            if down:
                self.type_button(name)
            return
        # Anything else falls through to Drive mode, so pedals, paddles and
        # gears keep working while typing.
        self.typer.finalize()
        self.drive_button(name, down)

    def pedal(self, name, down):
        if not self.armed:
            return
        self.typer.finalize()
        feet = self.cfg["feet"]
        if name == "clutch":
            if down:
                self.clutch_down_at = time.monotonic()
                self.clutch_used = False
            else:
                held_ms = (time.monotonic() - (self.clutch_down_at or 0)) * 1000
                if not self.clutch_used and held_ms <= feet["clutch_tap_max_ms"]:
                    self.out.press(feet["clutch_tap"])
                self.clutch_down_at = None
        elif down:
            self.out.press(feet[name])

    # --- drive mode -----------------------------------------------------

    def drive_button(self, name, down):
        if name.startswith("gear_"):
            if down:
                self.shift(name)
            return
        if name in ("plus", "minus"):
            if down:
                self.step_effort(1 if name == "plus" else -1)
            return
        action = self.cfg["drive"].get(name)
        if not action:
            return
        if action.startswith("hold:"):
            key = action[5:]
            self.out.hold(key, down)
            (self.held.add if down else self.held.discard)(key)
        elif down:
            self.out.press(action)

    # --- type mode ------------------------------------------------------

    def type_button(self, name):
        t = self.typer
        actions = {
            "prev": lambda: t.step(-1), "next": lambda: t.step(1),
            "set_prev": lambda: t.change_set(-1), "set_next": lambda: t.change_set(1),
            "commit": t.commit, "space": t.space, "backspace": t.backspace,
            "suggest": t.next_suggestion, "accept": t.accept,
        }
        actions[self.cfg["type_keys"][name]]()

    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.typer.finalize()
        self.mode = mode
        self.fb.notify(f"{mode.title()} mode",
                       "D-pad picks, dial Enter commits" if mode == "type" else "")

    # --- shifter and effort ---------------------------------------------

    def shift(self, gear):
        if self.cfg["shifter"]["require_clutch"] and self.clutch_down_at is None:
            self.fb.grind()
            return
        self.clutch_used = True
        commands = self.cfg["shifter"].get(gear, [])
        label = gear.removeprefix("gear_").upper()
        self.fb.notify(f"Gear {label}", ", ".join(commands))
        self.run_commands(commands)

    def step_effort(self, step):
        levels = self.cfg["effort"]["levels"]
        new = max(0, min(len(levels) - 1, self.effort + step))
        if new == self.effort:
            self.fb.notify("Effort", f"already {levels[new]}")
            return
        self.effort = new
        self.fb.notify("Effort", levels[new])
        self.run_commands([f"/effort {levels[new]}"])

    def run_commands(self, commands):
        """Send slash commands without losing the user's draft prompt."""
        if commands == ["@rewind"]:
            self.out.press("esc")
            self.out.press("esc")
            return
        # A placeholder keeps the input non-empty, so Ctrl+S always stashes
        # (on an empty prompt it would restore an older stash instead).
        self.out.type("x")
        self.out.press("ctrl+s")
        for cmd in commands:
            self.out.type(cmd)
            self.out.press("enter")
            self.out.pause(0.25)
        self.out.press("ctrl+s")
        self.out.press("backspace")

    # --- state ----------------------------------------------------------

    def set_armed(self, armed):
        self.armed = armed
        if not armed:
            self.release_all()
        self.fb.notify("G923 armed" if armed else "G923 disarmed",
                       f"{self.mode.title()} mode" if armed else "Inputs ignored")

    def release_all(self):
        for key in list(self.held):
            self.out.hold(key, False)
        self.held.clear()

    # --- steering scroll --------------------------------------------------

    async def scroll_loop(self):
        s = self.cfg["steering"]
        dt = 1 / 60
        while True:
            await asyncio.sleep(dt)
            if not self.armed:
                self.scroll_acc = 0.0
                continue
            deg = (self.steer_value - s["center"]) / s["units_per_degree"]
            mag = abs(deg) - s["deadzone_deg"]
            if mag <= 0:
                self.scroll_acc = 0.0
                continue
            frac = min(mag / (s["full_speed_deg"] - s["deadzone_deg"]), 1.0)
            rate = s["max_rate"] * frac ** 1.5
            # Turning left (negative) scrolls up (positive wheel).
            sign = -1 if deg > 0 else 1
            if s.get("invert"):
                sign = -sign
            self.scroll_acc += sign * rate * dt
            notches = int(self.scroll_acc)
            if notches:
                self.scroll_acc -= notches
                self.out.scroll(notches)

    # --- main loop --------------------------------------------------------

    async def read_loop(self, dev):
        async for ev in dev.async_read_loop():
            self.handle(ev)

    async def run(self):
        name = self.cfg["device"]["name_match"]
        scroller = asyncio.create_task(self.scroll_loop())
        try:
            while True:
                dev = find_device(name)
                if not dev:
                    print(f"waiting for a device matching {name!r}...", flush=True)
                    await asyncio.sleep(2)
                    continue
                print(f"connected: {dev.name} ({dev.path})", flush=True)
                try:
                    await self.read_loop(dev)
                except OSError:
                    print("device disconnected", flush=True)
                    self.release_all()
                    await asyncio.sleep(1)
        finally:
            scroller.cancel()


def dump(cfg):
    dev = find_device(cfg["device"]["name_match"])
    if not dev:
        sys.exit("wheel not found")
    print(f"{dev.name} ({dev.path}) - Ctrl+C to stop")
    names = cfg["buttons"]
    last = {}
    for ev in dev.read_loop():
        if ev.type == e.EV_KEY:
            print(f"KEY {ev.code:4d} {names.get(ev.code, '?'):<12} {'down' if ev.value else 'up'}")
        elif ev.type == e.EV_ABS:
            axis = e.ABS[ev.code]
            step = 1000 if axis == "ABS_X" else 20
            if axis.startswith("ABS_HAT") or abs(ev.value - last.get(axis, -9999)) >= step:
                last[axis] = ev.value
                print(f"ABS {axis:<10} {ev.value}")


def main():
    ap = argparse.ArgumentParser(prog="g923d", description=__doc__)
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--dry-run", action="store_true", help="print actions instead of sending keys")
    ap.add_argument("--dump", action="store_true", help="print raw wheel events")
    args = ap.parse_args()
    cfg = config.load(args.config)
    if args.dump:
        return dump(cfg)
    out = DryRun() if args.dry_run else Injector()
    daemon = Daemon(cfg, out, Feedback(cfg))
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        daemon.release_all()
        out.close()


if __name__ == "__main__":
    main()

import argparse
import asyncio
import json
import os
import sys
import time
from collections import deque

import evdev
from evdev import ecodes as e

from . import config
from .feedback import Feedback
from .keys import DryRun, Injector
from .typing import SET_NAMES, Typer

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
        self.amount = 0.0

    def update(self, value):
        amount = self.amount = (255 - value) / 255
        if not self.down and amount >= self.press_at:
            self.down = True
            return "press"
        if self.down and amount <= self.release_at:
            self.down = False
            return "release"
        return None


class Daemon:
    def __init__(self, cfg, out, fb, typer=None, claude=None):
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
        claude = claude or {}  # Claude Code's settings.json, for the starting model and effort
        eff = cfg["effort"]
        start = claude.get("effortLevel", eff["start"])
        self.effort = eff["levels"].index(start if start in eff["levels"] else eff["start"])
        self.claude_state = "idle"  # idle | busy | attention, from hooks
        self.backspace_task = None
        self.busy_since = None
        self.done_at = None
        self.model = claude.get("model")  # updated when you shift gear
        self.gear = None                   # the gear the shifter is in, if any
        self.connected = False
        self.watchers = set()
        # What Claude is up to, from the hooks' details (shown on the HUD).
        self.session = None    # project folder name
        self.task = None       # the prompt being worked on
        self.tool = None       # {"name", "detail"} of the tool running now
        self.approval = None   # {"name", "detail"} of the tool waiting for a yes
        self.trail = deque(maxlen=4)  # names of the last few finished tools
        self.steps = 0

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
        if name.startswith("gear_"):
            label = name.removeprefix("gear_").upper()
            if down:
                self.gear = label
            elif self.gear == label:
                self.gear = None
        if down and self.armed and self.claude_state == "attention":
            self.claude_state = "busy"
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
            is_backspace = self.cfg["type_keys"][name] == "backspace"
            if down:
                if not is_backspace:
                    self.stop_backspace()  # don't let the repeat eat what you just typed
                self.type_button(name)
            if is_backspace:
                self.hold_backspace(down)
            return
        # Anything else falls through to Drive mode, so pedals, paddles and
        # gears keep working while typing.
        self.finalize()
        self.drive_button(name, down)

    def pedal(self, name, down):
        if not self.armed:
            return
        if down and self.claude_state == "attention":
            self.claude_state = "busy"
        self.finalize()
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
            "more": t.more, "pick_left": lambda: t.pick("left"),
            "pick_center": lambda: t.pick("center"), "pick_right": lambda: t.pick("right"),
            # names from before the suggestion bar, so older configs keep working
            "suggest": t.more, "accept": lambda: t.pick("center"),
        }
        actions[self.cfg["type_keys"][name]]()

    def hold_backspace(self, down):
        # One delete per press; holding it repeats like a keyboard key.
        if not down:
            self.stop_backspace()
            return
        if self.backspace_task:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no event loop when a test drives the daemon directly
        self.backspace_task = loop.create_task(self.backspace_loop())

    async def backspace_loop(self):
        t = self.cfg["type"]
        held = time.monotonic()
        await asyncio.sleep(t["backspace_repeat_after_s"])
        while True:
            if time.monotonic() - held >= t["backspace_word_after_s"]:
                self.typer.backspace_word()
                await asyncio.sleep(t["backspace_word_rate_s"])
            else:
                self.typer.backspace()
                await asyncio.sleep(t["backspace_repeat_rate_s"])

    def stop_backspace(self):
        if self.backspace_task:
            self.backspace_task.cancel()
            self.backspace_task = None

    def finalize(self):
        """Drop any held backspace and leave the visible prompt alone."""
        self.stop_backspace()
        self.typer.finalize()

    def set_mode(self, mode):
        if mode == self.mode:
            return
        self.finalize()
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
        for cmd in commands:
            if cmd.startswith("/model "):
                self.model = cmd.split(None, 1)[1]
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
        self.fb.armed(armed)
        self.fb.notify("G923 armed" if armed else "G923 disarmed",
                       f"{self.mode.title()} mode" if armed else "Inputs ignored")

    def release_all(self):
        self.stop_backspace()
        for key in list(self.held):
            self.out.hold(key, False)
        self.held.clear()

    # --- Claude Code hooks ------------------------------------------------

    def hook_event(self, msg, info=None):
        """Handle a message from bin/g923-ping (sent by Claude Code hooks).

        `info` is what the hook told us: the session, prompt, tool and detail.
        """
        info = info or {}
        if info.get("session"):
            self.session = info["session"]
        tool = {"name": info["tool"], "detail": info.get("detail", "")} if info.get("tool") else None
        if msg == "busy":
            if self.claude_state == "idle":
                self.busy_since = time.time()
            if "prompt" in info:  # a new prompt starts a new turn
                self.task = info["prompt"]
                self.tool, self.approval = None, None
                self.trail.clear()
                self.steps = 0
            self.claude_state = "busy"
        elif msg == "tool":
            if self.claude_state == "idle":
                self.busy_since = time.time()
                self.claude_state = "busy"
            self.tool = tool
        elif msg == "done":
            self.claude_state = "idle"
            self.busy_since = None
            self.done_at = time.time()
            self.tool, self.approval = None, None
            self.fb.done()
        elif msg == "attention":
            self.approval = tool or self.tool
            if self.claude_state != "attention":
                self.claude_state = "attention"
                self.fb.notify("Claude needs you", "Permission requested")
                self.fb.attention()
        elif msg == "clear":
            finished = tool or self.tool
            if finished:
                self.trail.append(finished["name"])
                self.steps += 1
            self.tool, self.approval = None, None
            if self.claude_state == "attention":
                self.claude_state = "busy"
        else:
            print(f"unknown hook message: {msg!r}", flush=True)

    async def serve_hooks(self):
        path = config.expand(self.cfg["feedback"]["socket"])
        if os.path.exists(path):
            os.unlink(path)

        async def client(reader, writer):
            try:
                data = await asyncio.wait_for(reader.read(4096), timeout=1)
                if data.strip() == b"watch":
                    await self.watch(reader, writer)
                    return
                # Hooks send one message and hang up, so read to the end.
                data += await asyncio.wait_for(reader.read(), timeout=1)
                text = data.decode(errors="replace").strip()
                if text.startswith("{"):
                    try:
                        info = json.loads(text)
                    except ValueError:
                        info = {}
                    if isinstance(info, dict) and isinstance(info.get("event"), str):
                        self.hook_event(info["event"], info)
                    return
                for msg in text.split():
                    self.hook_event(msg)
            except (asyncio.TimeoutError, ConnectionError):
                pass
            finally:
                writer.close()

        server = await asyncio.start_unix_server(client, path=path)
        os.chmod(path, 0o600)
        print(f"listening for hooks on {path}", flush=True)
        return server

    # --- live state for the HUD -------------------------------------------

    def snapshot(self):
        """Everything the on-screen HUD shows, as plain JSON-able data."""
        s = self.cfg["steering"]
        levels = self.cfg["effort"]["levels"]
        t = self.typer
        notice = vars(self.fb).get("last_notice")
        return {
            "connected": self.connected,
            "armed": self.armed,
            "mode": self.mode,
            "claude": self.claude_state,
            "busy_since": self.busy_since,
            "done_at": self.done_at,
            "gear": self.gear,
            "model": self.model,
            "effort": levels[self.effort],
            "effort_levels": levels,
            "steer_deg": round((self.steer_value - s["center"]) / s["units_per_degree"], 1),
            "pedals": {n: round(p.amount, 2) for n, p in self.pedals.items()},
            "type": {
                "set": SET_NAMES[t.set_idx] if t.set_idx < len(SET_NAMES) else f"set {t.set_idx + 1}",
                "chars": t.charset,
                "idx": t.char_idx,
                "preview": t.preview,
                "word": t.word,
                "suggestions": t.suggestions() if self.mode == "type" else None,
            },
            "notice": notice,
            "session": self.session,
            "task": self.task,
            "tool": self.tool,
            "approval": self.approval,
            "trail": list(self.trail),
            "steps": self.steps,
        }

    async def watch(self, reader, writer):
        """Stream state to a HUD until it disconnects."""
        self.watchers.add(writer)
        try:
            writer.write((json.dumps(self.snapshot()) + "\n").encode())
            while await reader.read(256):
                pass
        except ConnectionError:
            pass
        finally:
            self.watchers.discard(writer)

    async def publish_loop(self):
        """Send the state to watching HUDs whenever it changes (up to 30 times a second)."""
        last = None
        while True:
            await asyncio.sleep(1 / 30)
            if not self.watchers:
                last = None
                continue
            line = json.dumps(self.snapshot())
            if line == last:
                continue
            last = line
            for w in list(self.watchers):
                if w.is_closing() or w.transport.get_write_buffer_size() > 1 << 16:
                    self.watchers.discard(w)  # gone, or too slow to keep up
                    w.close()
                    continue
                w.write((line + "\n").encode())

    async def attention_loop(self):
        """Keep nudging while a permission prompt is waiting."""
        every = self.cfg["feedback"]["attention_repeat_s"]
        while True:
            await asyncio.sleep(every)
            if self.claude_state == "attention":
                self.fb.attention()

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
        server = await self.serve_hooks()
        tasks = [asyncio.create_task(self.scroll_loop()),
                 asyncio.create_task(self.attention_loop()),
                 asyncio.create_task(self.publish_loop())]
        try:
            while True:
                dev = find_device(name)
                if not dev:
                    print(f"waiting for a device matching {name!r}...", flush=True)
                    await asyncio.sleep(2)
                    continue
                print(f"connected: {dev.name} ({dev.path})", flush=True)
                self.connected = True
                if hasattr(self.fb, "attach_wheel"):
                    self.fb.attach_wheel(dev, self.cfg.get("ffb", {}))
                try:
                    await self.read_loop(dev)
                except OSError:
                    print("device disconnected", flush=True)
                    self.release_all()
                    await asyncio.sleep(1)
                finally:
                    self.connected = False
                    if hasattr(self.fb, "detach_wheel"):
                        self.fb.detach_wheel()
        finally:
            for t in tasks:
                t.cancel()
            server.close()


def claude_settings(cfg):
    """Claude Code's own settings, for the model and effort it starts with."""
    path = cfg.get("hud", {}).get("claude_settings", "~/.claude/settings.json")
    try:
        with open(config.expand(path)) as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


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


def test_ffb(cfg, which):
    from .ffb import EFFECTS, Wheel

    dev = find_device(cfg["device"]["name_match"])
    if not dev:
        sys.exit("wheel not found")
    wheel = Wheel(dev, cfg.get("ffb", {}))
    if not wheel.ok:
        sys.exit("no force feedback on this wheel; is the new-lg4ff driver loaded?")
    for name in (EFFECTS if which == "all" else [which]):
        print(f"playing {name}", flush=True)
        wheel.play(name)
        time.sleep(1.6)
    wheel.close()


def main():
    ap = argparse.ArgumentParser(prog="g923d", description=__doc__)
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--dry-run", action="store_true", help="print actions instead of sending keys")
    ap.add_argument("--dump", action="store_true", help="print raw wheel events")
    ap.add_argument("--test-ffb", metavar="EFFECT", nargs="?", const="all",
                    help="play force-feedback effects (done, attention, grind, arm, disarm, all)")
    args = ap.parse_args()
    cfg = config.load(args.config)
    if args.dump:
        return dump(cfg)
    if args.test_ffb:
        return test_ffb(cfg, args.test_ffb)
    out = DryRun() if args.dry_run else Injector()
    daemon = Daemon(cfg, out, Feedback(cfg), claude=claude_settings(cfg))
    try:
        asyncio.run(daemon.run())
    except KeyboardInterrupt:
        pass
    finally:
        daemon.release_all()
        out.close()


if __name__ == "__main__":
    main()

"""Floating HUD: a card in the corner of the screen that's half agent status,
half racing dash.

The left side says, in plain words, what Claude is doing ("Editing hud.py",
"Running npm test"), the task, the last few steps and a timer, under a strip
of F1-style rev lights that rev while Claude works, flash amber when it needs
you and go green when it's done. When Claude asks for permission it shows the
command and which wheel buttons answer it. The right side is a dial whose rim
turns with your wheel, with the gear in the middle, effort as the tach arc and
the model underneath, next to live pedal bars. In Type mode the left side
shows the word and the letter picker instead.

It never takes focus and clicks go straight through it, so the wheel's key
presses still reach Claude. Drag it anywhere (it remembers), double-click to
snap it back to its corner, and hover for a × that closes it. It watches g923d
over the same socket the Claude Code hooks use. Needs the optional Qt dependency: pipx install 'g923-claude[hud]'.
"""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

from . import config

W, H = 460, 150     # the card; the window adds room for its shadow
PAD = 14
FONT_DIR = Path(__file__).with_name("fonts")
POSITION_FILE = (Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local" / "state")
                 / "g923-claude" / "hud.json")
COLORS = {
    "card": "#17171b", "text": "#f4f4f5", "muted": "#a1a1aa", "faint": "#71717a",
    "line": "#2a2a31", "chip": "#24242a",
    "coral": "#e07b53", "amber": "#f5a524", "green": "#3fb950", "red": "#f04f5a",
    "led_green": "#22e06b", "led_red": "#ff2e45", "led_blue": "#3d7bff",
    # PlayStation button colours, as on the wheel
    "cross": "#7aa7ff", "circle": "#ff6b81", "square": "#d6a2e8", "triangle": "#4fd1c5",
}
RACING = ["Titillium Web"]   # bundled; for the gear and model, like a race car's dash
LEDS = ["led_green"] * 5 + ["led_red"] * 5 + ["led_blue"] * 5   # an F1 wheel's shift lights
SANS = ["Inter", "Adwaita Sans", "Segoe UI Variable Text", "Segoe UI", "SF Pro Text",
        "Helvetica Neue", "Arial"]
MONO = ["JetBrains Mono", "Adwaita Mono", "Cascadia Mono", "SF Mono", "Menlo", "Consolas",
        "monospace"]
VERBS = {
    "Bash": "Running", "Edit": "Editing", "MultiEdit": "Editing", "Write": "Writing",
    "NotebookEdit": "Editing", "Read": "Reading", "Grep": "Searching", "Glob": "Finding",
    "WebFetch": "Fetching", "WebSearch": "Searching the web for", "Task": "Delegating",
    "Agent": "Delegating", "TodoWrite": "Planning",
}
MODEL_NAMES = {"opus[1m]": "Opus 1M", "opusplan": "Opus Plan"}
PEDAL_COLORS = {"clutch": "led_blue", "brake": "led_red", "gas": "led_green"}
TEXT_X, TEXT_RIGHT = 40, 288   # the agent half of the card
DIAL_X = 388                   # the racing half
DONE_FOR = 8.0     # seconds "Done" stays up before settling back to "Ready"
NOTICE_FOR = 2.5   # seconds a gear or effort change stays up
OFFLINE = {"daemon": False}


def model_name(model):
    """'opus' -> Opus, 'claude-sonnet-5-5[1m]' -> Sonnet 5.5 1M."""
    if not model:
        return "No model"
    if model in MODEL_NAMES:
        return MODEL_NAMES[model]
    model, long_context = model.removesuffix("[1m]"), model.endswith("[1m]")
    parts = model.removeprefix("claude-").split("-")
    words = [p.capitalize() for p in parts if not p.isdigit()]
    version = ".".join(p for p in parts if p.isdigit() and len(p) <= 2)  # drop date stamps
    name = " ".join(words + [version] if version else words)
    return name + (" 1M" if long_context else "")


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def tool_label(name):
    """'mcp__github__create_issue' -> 'github'; everything else stays as is."""
    if name.startswith("mcp__"):
        return name.split("__")[1] if name.count("__") >= 2 else name[5:]
    return name


def doing(tool, details=True):
    """A plain-words headline for a running tool: 'Editing hud.py'."""
    name = tool.get("name", "")
    detail = tool.get("detail", "") if details else ""
    if name.startswith("mcp__"):
        return f"Using {tool_label(name)}"
    verb = VERBS.get(name)
    if not verb:
        return f"Using {name}"
    if name in ("Grep", "WebSearch") and detail:
        detail = f"“{detail}”"
    return f"{verb} {detail}" if detail else f"{verb}…"


def load_position(path=POSITION_FILE):
    """Where the HUD was last dragged to, as (x, y), or None."""
    try:
        data = json.loads(Path(path).read_text())
        return int(data["x"]), int(data["y"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def save_position(pos, path=POSITION_FILE):
    """Remember where the HUD was dragged; None forgets it (back to the corner)."""
    path = Path(path)
    try:
        if pos is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"x": pos[0], "y": pos[1]}))
    except OSError:
        pass


def lit_leds(phase, now, busy_since=None, done_at=None):
    """How the 15 rev lights look right now: (how many are lit, brightness)."""
    n = len(LEDS)
    if phase == "approval":
        return n, 1.0 if int(now * 3) % 2 else 0.2       # flashing, like a pit limiter
    if phase in ("thinking", "working"):
        period = 2.4 if phase == "thinking" else 1.4     # revs faster while a tool runs
        t = ((now - (busy_since or now)) % period) / period
        return round(n * (0.5 - 0.5 * math.cos(2 * math.pi * t))), 1.0
    if phase == "done" and done_at:
        return n, max(0.0, 1 - (now - done_at) / DONE_FOR)
    return 0, 0.0


def demo_state(t):
    """A scripted ~24 s loop through everything the HUD can show."""
    t %= 24
    now = time.time()
    s = {
        "daemon": True, "connected": True, "armed": True, "mode": "drive",
        "claude": "idle", "busy_since": None, "done_at": None,
        "gear": None, "model": "sonnet", "effort": "high",
        "effort_levels": ["low", "medium", "high", "xhigh", "max"],
        "steer_deg": round(70 * math.sin(t * 0.7), 1),
        "pedals": {"clutch": 1.0 if 2 <= t < 4 else 0.0,
                   "brake": round(max(0.0, -math.sin(t * 0.9 + 0.4)) ** 3, 2),
                   "gas": round(max(0.0, math.sin(t * 0.9)) ** 0.7, 2)},
        "type": {"set": "abc", "chars": "abcdefghijklmnopqrstuvwxyz", "idx": 0,
                 "preview": None, "word": ""},
        "notice": None, "session": "g923-claude", "task": None, "tool": None,
        "approval": None, "trail": [], "steps": 0,
    }
    if t >= 3:
        s["gear"], s["model"] = "3", "opus"
        s["notice"] = {"title": "Gear 3", "body": "/model opus", "at": now - (t - 3)}
    steps = [(6.5, "Read", "hud.py"), (8, "Grep", "paintEvent"), (9.5, "Edit", "hud.py"),
             (10, "Bash", "python -m unittest discover -s tests")]
    if t >= 5:
        s.update(task="make the HUD easier to read at a glance", claude="busy",
                 busy_since=now - (t - 5))
        for i, (start, name, detail) in enumerate(steps):
            if t >= start:
                s["tool"] = {"name": name, "detail": detail}
                s["trail"] = [x[1] for x in steps[:i]]
                s["steps"] = i
        if 10 <= t < 12:
            s["claude"], s["approval"] = "attention", {"name": "Bash", "detail": steps[3][2]}
        if t >= 13.5:
            s["tool"], s["trail"], s["steps"] = None, [x[1] for x in steps], 4
    if t >= 14.5:
        s.update(claude="idle", busy_since=None, done_at=now - (t - 14.5), tool=None)
    if t >= 17:
        word = "refactor"[: int((t - 17) * 1.4)]
        nxt = "refactor"[len(word) % 8]
        s["mode"] = "type"
        preview = nxt if int(t * 3) % 2 else None
        typed = word + (preview or "")
        words = [w for w in ("refactor", "readme", "reference", "repo", "refresh") if w.startswith(typed)]
        words += [None] * 3
        s["type"].update(word=word, idx=s["type"]["chars"].index(nxt), preview=preview,
                         suggestions={"left": words[1], "center": words[0], "right": words[2]})
    return s


def build(args, cfg):
    # On GNOME/Wayland an app can't choose where its window goes or keep it
    # on top, so run through XWayland, where it can.
    if (sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY")
            and os.environ.get("DISPLAY") and "QT_QPA_PLATFORM" not in os.environ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    try:
        from PySide6.QtCore import QPoint, QPointF, QRectF, Qt, QTimer
        from PySide6.QtGui import (QBrush, QColor, QFont, QFontDatabase, QFontMetricsF,
                                   QGuiApplication, QLinearGradient, QPainter, QPainterPath,
                                   QPen, QRadialGradient)
        from PySide6.QtNetwork import QLocalSocket
        from PySide6.QtWidgets import QApplication, QWidget
    except ImportError:
        sys.exit("g923-hud needs Qt: pipx install 'g923-claude[hud]' "
                 "(or pip install PySide6-Essentials)")

    hud_cfg = cfg.get("hud", {})
    details = hud_cfg.get("show_details", True)

    def color(name, alpha=1.0):
        c = QColor(COLORS.get(name, name))
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        return c

    def font(size, weight=QFont.Weight.Normal, mono=False, tabular=False, racing=False):
        f = QFont()
        f.setFamilies(RACING + SANS if racing else MONO if mono else SANS)
        f.setPixelSize(round(size))
        f.setWeight(QFont.Weight.Bold if racing else weight)
        f.setItalic(racing)
        if tabular:
            f.setFeature(QFont.Tag("tnum"), 1)  # digits that don't jiggle as the timer runs
        return f

    def pen(c, width):
        return QPen(c, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)

    def text(p, x, y, s, f, c, max_width=None, elide=Qt.TextElideMode.ElideRight):
        """Draw s with its baseline at y; returns the x where it ends."""
        fm = QFontMetricsF(f)
        if max_width is not None:
            s = fm.elidedText(s, elide, max(0.0, max_width))
        p.setFont(f)
        p.setPen(c)
        p.drawText(QPointF(x, y), s)
        return x + fm.horizontalAdvance(s)

    class Feed:
        """Follows g923d's state, reconnecting whenever the daemon restarts."""

        def __init__(self, path, on_state):
            self.path, self.on_state = path, on_state
            self.buf = b""
            self.sock = QLocalSocket()
            self.sock.connected.connect(lambda: self.sock.write(b"watch"))
            self.sock.readyRead.connect(self.read)
            self.sock.disconnected.connect(self.lost)
            self.sock.errorOccurred.connect(lambda _: self.lost())
            self.retry = QTimer(singleShot=True, interval=2000)
            self.retry.timeout.connect(self.connect)
            self.connect()

        def connect(self):
            self.buf = b""
            self.sock.abort()
            self.sock.connectToServer(self.path)

        def lost(self):
            self.on_state(dict(OFFLINE))
            if not self.retry.isActive():
                self.retry.start()

        def read(self):
            self.buf += bytes(self.sock.readAll())
            *lines, self.buf = self.buf.split(b"\n")
            if lines:
                try:
                    state = json.loads(lines[-1])
                except ValueError:
                    return
                state["daemon"] = True
                self.on_state(state)

    class Hud(QWidget):
        def __init__(self, scale, opacity, click_through=False):
            super().__init__()
            self.scale, self.opacity = scale, opacity
            self.hover = False        # the mouse is over the card: show the close button
            self.over_close = False
            self.drag_from = None     # where the card was grabbed, while dragging
            self.state = dict(OFFLINE)
            self.steer = 0.0
            self.pedals = {"clutch": 0.0, "brake": 0.0, "gas": 0.0}
            self.busy_since = None
            self.last_turn = None     # how long the last turn took, for "Done in"
            self.gear_changed = 0.0   # when the gear last changed, for a little pop
            self.last_tick = time.monotonic()
            self.setWindowTitle("g923 HUD")
            # Never takes keyboard focus, so the wheel keeps typing into Claude
            # even while you drag the card around.
            flags = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                     | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                     | Qt.WindowType.X11BypassWindowManagerHint)
            if click_through:
                flags |= Qt.WindowType.WindowTransparentForInput
                self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            else:
                self.setMouseTracking(True)
                self.setCursor(Qt.CursorShape.OpenHandCursor)
            self.setWindowFlags(flags)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setFixedSize(round((W + 2 * PAD) * scale), round((H + 2 * PAD) * scale))
            self.timer = QTimer(interval=16)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

        def set_state(self, state):
            if state.get("busy_since"):
                self.busy_since = state["busy_since"]
            elif self.busy_since and state.get("done_at"):
                self.last_turn = state["done_at"] - self.busy_since
                self.busy_since = None
            if state.get("gear") and state.get("gear") != self.state.get("gear"):
                self.gear_changed = time.monotonic()
            self.state = state

        def tick(self):
            now = time.monotonic()
            k = min(1.0, (now - self.last_tick) * 14)
            self.last_tick = now
            s = self.state
            self.steer += (max(-450, min(450, s.get("steer_deg", 0.0))) - self.steer) * k
            for name in self.pedals:
                self.pedals[name] += (s.get("pedals", {}).get(name, 0.0) - self.pedals[name]) * k
            self.update()

        # --- mouse: drag to move, double-click for the corner, × to close ---

        def close_hit(self, pos):
            c = QPointF((PAD + W - 16) * self.scale, (PAD + 16) * self.scale)
            return (pos.x() - c.x()) ** 2 + (pos.y() - c.y()) ** 2 <= (11 * self.scale) ** 2

        def enterEvent(self, _):
            self.hover = True

        def leaveEvent(self, _):
            self.hover = self.over_close = False

        def mousePressEvent(self, e):
            if e.button() != Qt.MouseButton.LeftButton:
                return
            if self.close_hit(e.position()):
                QApplication.quit()
                return
            self.drag_from = e.globalPosition().toPoint() - self.pos()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)

        def mouseMoveEvent(self, e):
            if self.drag_from is not None:
                self.move(e.globalPosition().toPoint() - self.drag_from)
            self.over_close = self.close_hit(e.position())

        def mouseReleaseEvent(self, _):
            if self.drag_from is not None:
                self.drag_from = None
                self.setCursor(Qt.CursorShape.OpenHandCursor)
                save_position((self.x(), self.y()))

        def mouseDoubleClickEvent(self, _):
            save_position(None)
            place(self)

        # --- what to say ------------------------------------------------

        def phase(self, s):
            """offline | approval | working | thinking | done | idle"""
            if not s.get("daemon"):
                return "offline"
            if s.get("claude") == "attention":
                return "approval"
            if s.get("claude") == "busy":
                return "working" if s.get("tool") else "thinking"
            if s.get("done_at") and time.time() - s["done_at"] < DONE_FOR:
                return "done"
            return "idle"

        def headline(self, s, phase):
            if phase == "offline":
                return "g923d isn't running"
            if phase == "approval":
                name = (s.get("approval") or s.get("tool") or {}).get("name")
                return f"Allow {tool_label(name)}?" if name else "Claude needs your OK"
            if phase == "working":
                return doing(s["tool"], details)
            if phase == "thinking":
                return "Thinking…"
            if phase == "done":
                return f"Done in {clock(self.last_turn)}" if self.last_turn else "Done"
            return "Ready"

        # --- painting -------------------------------------------------

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
            p.scale(self.scale, self.scale)
            p.setOpacity(self.opacity)
            p.translate(PAD, PAD)
            s = self.state
            phase = self.phase(s)
            self.card(p, phase)
            self.rev_lights(p, s, phase)
            self.header(p, s, phase)
            if s.get("mode") == "type" and s.get("daemon"):
                self.typing(p, s)
            else:
                self.body(p, s, phase)
            self.footer(p, s)
            self.pedal_bars(p)
            self.dial(p, s, phase)
            if self.hover:
                self.close_button(p)
            p.end()

        def close_button(self, p):
            c = QPointF(W - 16, 16)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("red") if self.over_close else QColor(255, 255, 255, 40))
            p.drawEllipse(c, 9, 9)
            p.setPen(pen(QColor("#ffffff"), 1.6))
            for dx in (-3.2, 3.2):
                p.drawLine(QPointF(c.x() - dx, c.y() - 3.2), QPointF(c.x() + dx, c.y() + 3.2))

        def card(self, p, phase):
            r, slant = 18, 22
            path = QPainterPath()   # a slanted leading edge, like a racing livery
            path.moveTo(slant + r, 0)
            path.lineTo(W - r, 0)
            path.quadTo(W, 0, W, r)
            path.lineTo(W, H - r)
            path.quadTo(W, H, W - r, H)
            path.lineTo(r, H)
            path.quadTo(0, H, 2, H - r * 0.9)
            path.lineTo(slant - 2, r * 0.9)
            path.quadTo(slant, 0, slant + r, 0)
            for i in range(PAD, 0, -2):   # soft shadow
                shadow = QPainterPath()
                shadow.addRoundedRect(QRectF(-i + slant / 2, -i + 4, W + 2 * i - slant / 2, H + 2 * i), 20 + i, 20 + i)
                p.fillPath(shadow, QColor(0, 0, 0, 7))
            g = QLinearGradient(0, 0, 0, H)
            g.setColorAt(0, QColor(30, 30, 35, 245))
            g.setColorAt(1, QColor(18, 18, 22, 245))
            p.fillPath(path, g)
            edge = {"approval": color("amber", 0.6), "done": color("green", 0.4)}.get(phase)
            p.setPen(pen(edge or QColor(255, 255, 255, 22), 1.3 if edge else 1))
            p.drawPath(path)
            # Accent stripe parallel to the slanted edge, just inside it.
            top, bottom = QPointF(slant - 2, r * 0.9), QPointF(2, H - r * 0.9)

            def inside(y):
                t = (y - top.y()) / (bottom.y() - top.y())
                return QPointF(top.x() + (bottom.x() - top.x()) * t + 9, y)

            p.setPen(pen(color("coral", 0.9), 3))
            p.drawLine(inside(26), inside(H - 26))

        def rev_lights(self, p, s, phase):
            """F1 shift lights as the status light: they rev while Claude works,
            flash amber when it needs you and go green when it's done."""
            now = time.time()
            lit, level = lit_leds(phase, now, s.get("busy_since"), s.get("done_at"))
            for i, name in enumerate(LEDS):
                c = QPointF(TEXT_X + 4 + i * 16, 17)
                if phase == "approval":
                    name = "amber"
                elif phase == "done":
                    name = "led_green"
                on = i < lit and level > 0.05
                if on:
                    g = QRadialGradient(c, 9)
                    g.setColorAt(0, color(name, 0.5 * level))
                    g.setColorAt(1, color(name, 0.0))
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(g)
                    p.drawEllipse(c, 9, 9)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(name, 0.3 + 0.7 * level) if on else color("line"))
                p.drawEllipse(c, 3.8, 3.8)

        def header(self, p, s, phase):
            now = time.time()
            # The timer while Claude works, otherwise nothing: the dial has the rest.
            stamp = ""
            if phase in ("thinking", "working", "approval") and s.get("busy_since"):
                stamp = clock(now - s["busy_since"])
            f = font(12.5, QFont.Weight.DemiBold, tabular=True)
            stamp_w = QFontMetricsF(f).horizontalAdvance(stamp)
            if stamp:
                text(p, TEXT_RIGHT - stamp_w, 49, stamp, f, color("amber" if phase == "approval" else "coral"))

            title = self.headline(s, phase)
            f = font(16.5, QFont.Weight.DemiBold)
            if phase == "thinking":
                # A shimmer runs across "Thinking…", like a chat app's typing hint.
                tw = QFontMetricsF(f).horizontalAdvance(title)
                x = TEXT_X + ((now * 0.9) % 1.6 - 0.3) * tw
                g = QLinearGradient(x - 40, 0, x + 40, 0)
                g.setColorAt(0, color("muted"))
                g.setColorAt(0.5, color("text"))
                g.setColorAt(1, color("muted"))
                p.setFont(f)
                p.setPen(QPen(QBrush(g), 1))
                p.drawText(QPointF(TEXT_X, 49), title)
            else:
                text(p, TEXT_X, 49, title, f, color("text"), TEXT_RIGHT - stamp_w - 10 - TEXT_X)

        def body(self, p, s, phase):
            width = TEXT_RIGHT - TEXT_X
            # Second line: the task, or the command waiting for approval.
            line = None
            if phase == "approval":
                tool = s.get("approval") or s.get("tool") or {}
                self.code(p, TEXT_X, 58, tool.get("detail", "") if details else "", width)
            elif phase == "offline":
                line = "Start it: systemctl --user start g923d"
            elif s.get("task") and phase != "idle":
                line = f"“{s['task']}”" if details else "Working on your prompt"
            elif not s.get("connected"):
                line = "Plug in the wheel to drive"
            elif not s.get("armed"):
                line = "Wheel disarmed: press PS to drive"
            else:
                line = "Type a prompt, then press Gas to send"
            if line:
                text(p, TEXT_X, 72, line, font(12.5), color("muted"), width)

            # Third line: a gear or effort change, the wheel buttons that answer
            # Claude, or the steps so far.
            n = s.get("notice")
            if n and time.time() - n.get("at", 0) < NOTICE_FOR:
                grind = n["title"].startswith("Grind")
                msg = n["title"] + (f"  ·  {n['body']}" if n.get("body") else "")
                text(p, TEXT_X, 99, msg, font(12, QFont.Weight.Medium), color("red" if grind else "coral"), width)
            elif phase == "approval":
                self.buttons(p, TEXT_X, 99, [("cross", "Yes"), ("square", "Always"), ("circle", "No")])
            elif phase in ("thinking", "working", "done"):
                self.trail(p, s, phase)
            elif phase == "idle" and s.get("armed") and s.get("connected"):
                self.buttons(p, TEXT_X, 99, [("gas", "Send"), ("brake", "Stop")])

        def code(self, p, x, y, cmd, max_width):
            f = font(12, mono=True)
            fm = QFontMetricsF(f)
            cmd = fm.elidedText(cmd or "…", Qt.TextElideMode.ElideRight, max_width - 16)
            box = QRectF(x, y, fm.horizontalAdvance(cmd) + 16, 21)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("chip"))
            p.drawRoundedRect(box, 6, 6)
            text(p, x + 8, y + 15, cmd, f, color("text"))

        def buttons(self, p, x, y, items, size=12):
            """Wheel button hints: a coloured glyph (or a pedal's name), then what it does."""
            for key, label in items:
                if key in ("cross", "circle", "square"):
                    c = QPointF(x + 6, y - 4.5)
                    p.setPen(pen(color(key), 1.7))
                    p.setBrush(Qt.BrushStyle.NoBrush)
                    if key == "cross":
                        p.drawLine(QPointF(c.x() - 4, c.y() - 4), QPointF(c.x() + 4, c.y() + 4))
                        p.drawLine(QPointF(c.x() - 4, c.y() + 4), QPointF(c.x() + 4, c.y() - 4))
                    elif key == "circle":
                        p.drawEllipse(c, 4.6, 4.6)
                    else:
                        p.drawRect(QRectF(c.x() - 4, c.y() - 4, 8, 8))
                    x += 17
                else:
                    f = font(9.5, QFont.Weight.Bold)
                    w = QFontMetricsF(f).horizontalAdvance(key.upper()) + 10
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color("chip"))
                    p.drawRoundedRect(QRectF(x, y - 13, w, 17), 5, 5)
                    text(p, x + 5, y - 1.5, key.upper(), f, color("muted"))
                    x += w + 6
                x = text(p, x, y, label, font(size, QFont.Weight.Medium), color("text"), TEXT_RIGHT - x) + size * 1.3 - 1

        def trail(self, p, s, phase):
            """The last few steps, like breadcrumbs: Read ✓ › Grep ✓ › Bash."""
            items = [(name, "done") for name in s.get("trail", [])]
            if phase == "working" and s.get("tool"):
                items.append((s["tool"]["name"], "now"))
            if not items:
                if phase == "thinking":
                    self.buttons(p, TEXT_X, 99, [("brake", "Stop")])
                return
            f = font(11.5, QFont.Weight.Medium)
            x = TEXT_X
            for i, (name, state) in enumerate(items[-4:]):
                if i:
                    x = text(p, x + 4, 99, "›", f, color("faint")) + 4
                label = tool_label(name) + (" ✓" if state == "done" else "")
                x = text(p, x, 99, label, f, color("coral" if state == "now" else "muted"), TEXT_RIGHT - x)
            steps = s.get("steps", 0)
            label = f"{steps} step{'s' if steps != 1 else ''}"
            if phase == "done" and steps and x + 10 + QFontMetricsF(f).horizontalAdvance(label) <= TEXT_RIGHT:
                text(p, x + 10, 99, label, f, color("faint"))

        def typing(self, p, s):
            t = s.get("type") or {}
            chars, idx = t.get("chars") or "?", t.get("idx", 0)
            showing = t.get("preview") is not None
            # The word so far, with the letter you're on boxed at the end.
            x = text(p, TEXT_X, 77, t.get("word", ""), font(17, QFont.Weight.DemiBold), color("text"),
                     130, Qt.TextElideMode.ElideLeft) + 2
            box = QRectF(x, 59, 21, 25)
            p.setPen(pen(color("coral"), 1.5))
            p.setBrush(color("coral") if showing else color("coral", 0.12))
            p.drawRoundedRect(box, 5, 5)
            p.setFont(font(16, QFont.Weight.Bold))
            p.setPen(QColor("#ffffff") if showing else color("coral"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, chars[idx % len(chars)])

            # The letters either side, so you can see which way to turn the dial.
            cx = TEXT_RIGHT - 44
            for k in (-2, -1, 0, 1, 2):
                ch = chars[(idx + k) % len(chars)]
                p.setFont(font(15 if k == 0 else 12.5, QFont.Weight.Bold if k == 0 else QFont.Weight.Medium))
                p.setPen(color("coral") if k == 0 else color("muted", 0.8 if abs(k) == 1 else 0.4))
                p.drawText(QRectF(cx + k * 17 - 8, 58, 16, 22), Qt.AlignmentFlag.AlignCenter, ch)
            p.setPen(pen(color("coral", 0.7), 1.4))
            p.drawLine(QPointF(cx - 5, 82), QPointF(cx + 5, 82))
            self.suggestion_bar(p, t.get("suggestions") or {})

        def suggestion_bar(self, p, sugg):
            """Three words, like a phone keyboard: left paddle, △ and right paddle
            pick them, with the best guess in the middle."""
            gap = 5
            w = (TEXT_RIGHT - TEXT_X - 2 * gap) / 3
            for i, (slot, key) in enumerate((("left", "L"), ("center", "△"), ("right", "R"))):
                word = sugg.get(slot)
                box = QRectF(TEXT_X + i * (w + gap), 88, w, 21)
                best = slot == "center" and word
                p.setPen(pen(color("coral", 0.8), 1.2) if best else Qt.PenStyle.NoPen)
                p.setBrush(color("coral", 0.14) if best else color("chip"))
                p.drawRoundedRect(box, 7, 7)
                text(p, box.left() + 7, box.top() + 14.5, key, font(9.5, QFont.Weight.Bold),
                     color("triangle" if key == "△" else "faint"))
                if word:
                    text(p, box.left() + 21, box.top() + 15, word, font(12, QFont.Weight.DemiBold),
                         color("text" if best else "muted"), box.width() - 27)

        def footer(self, p, s):
            p.setPen(pen(color("line"), 1))
            p.drawLine(QPointF(TEXT_X - 4, 114), QPointF(TEXT_RIGHT, 114))
            y = 136
            if not s.get("daemon"):
                mode, c = "Offline", "faint"
            elif not s.get("connected"):
                mode, c = "Wheel off", "faint"
            elif not s.get("armed"):
                mode, c = "Disarmed · PS", "amber"
            elif s.get("mode") == "type":
                mode, c = f"Type · {(s.get('type') or {}).get('set', 'abc')}", "coral"
            else:
                mode, c = "Drive mode", "muted"
            f = font(11.5, QFont.Weight.DemiBold)
            w = QFontMetricsF(f).horizontalAdvance(mode) + 16
            chip = QRectF(TEXT_X, y - 15, w, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("chip"))
            p.drawRoundedRect(chip, 10, 10)
            text(p, chip.left() + 8, y - 1, mode, f, color(c))
            if s.get("mode") == "type" and s.get("daemon"):
                self.buttons(p, chip.right() + 10, y, [("cross", "space"), ("circle", "del"), ("square", "more")],
                             size=11)
            elif s.get("session"):
                text(p, chip.right() + 10, y - 1, s["session"], font(11.5), color("faint"),
                     TEXT_RIGHT - chip.right() - 10)

        def pedal_bars(self, p):
            for i, name in enumerate(("clutch", "brake", "gas")):
                x, top, h = TEXT_RIGHT + 12 + i * 8, 28, 96
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color("line"))
                p.drawRoundedRect(QRectF(x, top, 4.5, h), 2.25, 2.25)
                amount = max(0.0, min(1.0, self.pedals[name]))
                if amount > 0.02:
                    p.setBrush(color(PEDAL_COLORS[name]))
                    p.drawRoundedRect(QRectF(x, top + h * (1 - amount), 4.5, h * amount), 2.25, 2.25)
                f = font(8, QFont.Weight.Bold)
                p.setFont(f)
                p.setPen(color("faint"))
                p.drawText(QRectF(x - 4, top + h + 3, 12.5, 11), Qt.AlignmentFlag.AlignCenter, name[0].upper())

        def dial(self, p, s, phase):
            """The racing dial: a rim that turns with your wheel, the gear in the
            middle, effort as a tach arc around it and the model underneath."""
            cx, cy, r = DIAL_X, 68, 44
            now = time.time()
            glow_color = {"approval": "amber", "done": "led_green"}.get(phase)
            if glow_color:
                strength = (0.6 + 0.4 * math.sin(now * 8) if phase == "approval"
                            else 1 - (now - s["done_at"]) / DONE_FOR)
                g = QRadialGradient(QPointF(cx, cy), r + 20)
                g.setColorAt(0.6, color(glow_color, 0.0))
                g.setColorAt(0.8, color(glow_color, 0.35 * strength))
                g.setColorAt(1, color(glow_color, 0.0))
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(g)
                p.drawEllipse(QPointF(cx, cy), r + 20, r + 20)

            levels = s.get("effort_levels") or []
            if s.get("effort") in levels:
                level, n = levels.index(s["effort"]), len(levels)
                span, gap = 240 / n, 5
                for i in range(n):
                    start = 210 - i * span - gap / 2
                    c = color("led_red" if i == n - 1 else "coral") if i <= level else color("line")
                    p.setPen(QPen(c, 3.5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                    p.drawArc(QRectF(cx - r - 9, cy - r - 9, 2 * (r + 9), 2 * (r + 9)),
                              round(start * 16), round(-(span - gap) * 16))

            p.save()   # the rim turns with the real wheel
            p.translate(cx, cy)
            p.rotate(self.steer)
            g = QLinearGradient(0, -r, 0, r)
            g.setColorAt(0, QColor("#4a4f5a"))
            g.setColorAt(1, QColor("#1d1f25"))
            p.setPen(QPen(QBrush(g), 8))
            p.setBrush(QColor(0, 0, 0, 80))
            p.drawEllipse(QPointF(0, 0), r - 4, r - 4)
            p.setPen(pen(QColor("#3b3f48"), 4))
            for side in (-1, 1):
                p.drawLine(QPointF(side * (r - 8), 0), QPointF(side * (r - 14), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("coral"))
            p.drawRoundedRect(QRectF(-3, -r - 1, 6, 10), 2, 2)
            p.restore()

            gear = s.get("gear")
            pop = max(0.0, 1 - (time.monotonic() - self.gear_changed) / 0.3)
            p.setFont(font(44 * (1 + 0.2 * pop), racing=True))
            p.setPen(color("coral") if gear else color("muted", 0.35))
            p.drawText(QRectF(cx - 32, cy - 32, 64, 58), Qt.AlignmentFlag.AlignCenter, gear or "N")
            if s.get("effort"):
                p.setFont(font(9, QFont.Weight.Bold))
                p.setPen(color("muted"))
                p.drawText(QRectF(cx - 30, cy + 18, 60, 12), Qt.AlignmentFlag.AlignCenter, s["effort"].upper())
            f = font(15, racing=True)
            name = QFontMetricsF(f).elidedText(model_name(s.get("model")).upper(),
                                               Qt.TextElideMode.ElideRight, 130)
            p.setFont(f)
            p.setPen(color("text"))
            p.drawText(QRectF(cx - 66, H - 30, 132, 20), Qt.AlignmentFlag.AlignCenter, name)

    def place(widget):
        screens = QGuiApplication.screens()
        want = hud_cfg.get("screen", "primary")
        screen = next((sc for sc in screens if sc.name() == want), None) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        m = hud_cfg.get("margin", 24) - PAD   # the shadow sits inside the margin
        corner = hud_cfg.get("corner", "bottom-right")
        x = geo.left() + m if "left" in corner else geo.right() - widget.width() - m + 1
        y = geo.top() + m if "top" in corner else geo.bottom() - widget.height() - m + 1
        widget.move(x, y)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("g923-hud")
    for ttf in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))
    hud = Hud(hud_cfg.get("scale", 1.0), hud_cfg.get("opacity", 0.96),
              click_through=hud_cfg.get("click_through", False))

    if args.screenshot:
        hud.set_state(demo_state(args.at))
        hud.last_turn = 9.5   # the demo's turn, from prompt to done
        hud.gear_changed = 0.0
        hud.steer, hud.pedals = hud.state["steer_deg"], dict(hud.state["pedals"])
        hud.grab().save(args.screenshot)
        return None, hud
    if args.demo:
        start = time.monotonic()
        demo = QTimer(interval=50)
        demo.timeout.connect(lambda: hud.set_state(demo_state(time.monotonic() - start)))
        demo.start()
        hud._demo = demo
    else:
        hud._feed = Feed(config.expand(cfg["feedback"]["socket"]), hud.set_state)
    saved = load_position()
    centre = saved and QPoint(saved[0] + hud.width() // 2, saved[1] + hud.height() // 2)
    if saved and QGuiApplication.screenAt(centre):   # still on a connected screen
        hud.move(*saved)
    else:
        place(hud)
    hud.show()
    return app, hud


def main():
    ap = argparse.ArgumentParser(prog="g923-hud", description=__doc__.split("\n\n")[0])
    ap.add_argument("--config", help="path to config.toml")
    ap.add_argument("--demo", action="store_true", help="play a scripted demo instead of following g923d")
    ap.add_argument("--screenshot", metavar="PNG", help="save one demo frame to a PNG and exit")
    ap.add_argument("--at", type=float, default=7.0, help="demo time (seconds) for --screenshot")
    args = ap.parse_args()
    if args.screenshot:
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app, _ = build(args, config.load(args.config))
    if app:
        sys.exit(app.exec())


if __name__ == "__main__":
    main()

"""Floating HUD: a small card in the corner of the screen that says what Claude
is doing and what the wheel will do next.

It reads like an agent status card: what Claude is doing right now ("Editing
hud.py", "Running npm test"), the task it's on, the last few steps, and a
timer. When Claude asks for permission it shows the command and which wheel
buttons answer it. The footer has the gear, model, effort and mode, with a
small wheel that turns with yours. In Type mode it shows the word and the
letter picker instead.

It never takes focus and clicks go straight through it, so the wheel's key
presses still reach Claude. It watches g923d over the same socket the Claude
Code hooks use. Needs the optional Qt dependency: pipx install 'g923-claude[hud]'.
"""

import argparse
import json
import math
import os
import sys
import time

from . import config

W, H = 380, 150     # the card; the window adds room for its shadow
PAD = 14
COLORS = {
    "card": "#17171b", "text": "#f4f4f5", "muted": "#a1a1aa", "faint": "#71717a",
    "line": "#2a2a31", "chip": "#24242a",
    "coral": "#e07b53", "amber": "#f5a524", "green": "#3fb950", "red": "#f04f5a",
    # PlayStation button colours, as on the wheel
    "cross": "#7aa7ff", "circle": "#ff6b81", "square": "#d6a2e8", "triangle": "#4fd1c5",
}
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
        "pedals": {"clutch": 0.0, "brake": 0.0, "gas": 0.0},
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
        s["type"].update(word=word, idx=s["type"]["chars"].index(nxt),
                         preview=nxt if int(t * 3) % 2 else None)
    return s


def build(args, cfg):
    # On GNOME/Wayland an app can't choose where its window goes or keep it
    # on top, so run through XWayland, where it can.
    if (sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY")
            and os.environ.get("DISPLAY") and "QT_QPA_PLATFORM" not in os.environ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    try:
        from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
        from PySide6.QtGui import (QBrush, QColor, QFont, QFontMetricsF, QGuiApplication,
                                   QLinearGradient, QPainter, QPainterPath, QPen)
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

    def font(size, weight=QFont.Weight.Normal, mono=False, tabular=False):
        f = QFont()
        f.setFamilies(MONO if mono else SANS)
        f.setPixelSize(round(size))
        f.setWeight(weight)
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
        def __init__(self, scale, opacity):
            super().__init__()
            self.scale, self.opacity = scale, opacity
            self.state = dict(OFFLINE)
            self.steer = 0.0
            self.busy_since = None
            self.last_turn = None     # how long the last turn took, for "Done in"
            self.gear_changed = 0.0   # when the gear last changed, for a little pop
            self.last_tick = time.monotonic()
            self.setWindowTitle("g923 HUD")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                | Qt.WindowType.WindowTransparentForInput
                | Qt.WindowType.X11BypassWindowManagerHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
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
            self.steer += (max(-450, min(450, self.state.get("steer_deg", 0.0))) - self.steer) * k
            self.update()

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
            self.orb(p, phase)
            self.header(p, s, phase)
            if s.get("mode") == "type" and s.get("daemon"):
                self.typing(p, s)
            else:
                self.body(p, s, phase)
            self.footer(p, s)
            p.end()

        def card(self, p, phase):
            for i in range(PAD, 0, -2):   # soft shadow
                shadow = QPainterPath()
                shadow.addRoundedRect(QRectF(-i, -i + 4, W + 2 * i, H + 2 * i), 20 + i, 20 + i)
                p.fillPath(shadow, QColor(0, 0, 0, 7))
            path = QPainterPath()
            path.addRoundedRect(QRectF(0, 0, W, H), 20, 20)
            g = QLinearGradient(0, 0, 0, H)
            g.setColorAt(0, QColor(30, 30, 35, 245))
            g.setColorAt(1, QColor(20, 20, 24, 245))
            p.fillPath(path, g)
            edge = {"approval": color("amber", 0.55), "done": color("green", 0.35)}.get(phase)
            p.setPen(pen(edge or QColor(255, 255, 255, 22), 1.2 if edge else 1))
            p.drawPath(path)

        def orb(self, p, phase):
            """The status light: breathing when idle, spinning while Claude works,
            pinging when it needs you, a tick when it's done."""
            c, now = QPointF(30, 30), time.time()
            p.setPen(Qt.PenStyle.NoPen)
            if phase == "offline":
                p.setPen(pen(color("faint"), 1.5))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawEllipse(c, 6, 6)
            elif phase == "idle":
                p.setBrush(color("muted", 0.12))
                p.drawEllipse(c, 12, 12)
                p.setBrush(color("muted", 0.55 + 0.35 * math.sin(now * 2.1)))
                p.drawEllipse(c, 4.5, 4.5)
            elif phase in ("thinking", "working"):
                p.setBrush(color("coral", 0.16))
                p.drawEllipse(c, 12, 12)
                p.setBrush(color("coral"))
                p.drawEllipse(c, 4.5, 4.5)
                p.setPen(pen(color("coral"), 2.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawArc(QRectF(c.x() - 11, c.y() - 11, 22, 22), round(-now * 360 * 16) % 5760, 100 * 16)
            elif phase == "approval":
                ping = (now * 1.2) % 1.0
                p.setBrush(color("amber", 0.35 * (1 - ping)))
                p.drawEllipse(c, 6 + 10 * ping, 6 + 10 * ping)
                p.setBrush(color("amber"))
                p.drawEllipse(c, 6, 6)
            elif phase == "done":
                p.setBrush(color("green"))
                p.drawEllipse(c, 11, 11)
                tick = QPainterPath(QPointF(c.x() - 4.5, c.y() + 0.5))
                tick.lineTo(c.x() - 1.2, c.y() + 3.8)
                tick.lineTo(c.x() + 5, c.y() - 3.2)
                p.setPen(pen(QColor("#0d1f12"), 2.2))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(tick)

        def header(self, p, s, phase):
            now = time.time()
            right = W - 16
            # The timer while Claude works, otherwise the project's name.
            if phase in ("thinking", "working", "approval") and s.get("busy_since"):
                stamp = clock(now - s["busy_since"])
            else:
                stamp = s.get("session") or ""
            f = font(12, QFont.Weight.Medium, tabular=True)
            stamp_w = QFontMetricsF(f).horizontalAdvance(stamp)
            if stamp:
                text(p, right - stamp_w, 35, stamp, f, color("faint"))

            title = self.headline(s, phase)
            f = font(15.5, QFont.Weight.DemiBold)
            if phase == "thinking":
                # A shimmer runs across "Thinking…", like a chat app's typing hint.
                tw = QFontMetricsF(f).horizontalAdvance(title)
                x = 52 + ((now * 0.9) % 1.6 - 0.3) * tw
                g = QLinearGradient(x - 40, 0, x + 40, 0)
                g.setColorAt(0, color("muted"))
                g.setColorAt(0.5, color("text"))
                g.setColorAt(1, color("muted"))
                p.setFont(f)
                p.setPen(QPen(QBrush(g), 1))
                p.drawText(QPointF(52, 35), title)
            else:
                text(p, 52, 35, title, f, color("text"), right - stamp_w - 12 - 52)

        def body(self, p, s, phase):
            # Second line: the task, or the command waiting for approval.
            line = None
            if phase == "approval":
                tool = s.get("approval") or s.get("tool") or {}
                self.code(p, 52, 47, tool.get("detail", "") if details else "", W - 68)
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
                text(p, 52, 62, line, font(12.5), color("muted"), W - 68)

            # Third line: a gear or effort change, the wheel buttons that answer
            # Claude, or the steps so far.
            n = s.get("notice")
            if n and time.time() - n.get("at", 0) < NOTICE_FOR:
                grind = n["title"].startswith("Grind")
                msg = n["title"] + (f"  ·  {n['body']}" if n.get("body") else "")
                text(p, 52, 90, msg, font(12, QFont.Weight.Medium), color("red" if grind else "coral"), W - 68)
            elif phase == "approval":
                self.buttons(p, 52, 90, [("cross", "Yes"), ("square", "Always"), ("circle", "No")])
            elif phase in ("thinking", "working", "done"):
                self.trail(p, s, phase)
            elif phase == "idle" and s.get("armed") and s.get("connected"):
                self.buttons(p, 52, 90, [("gas", "Send"), ("brake", "Stop"), ("clutch", "Model")])

        def code(self, p, x, y, cmd, max_width):
            f = font(12, mono=True)
            fm = QFontMetricsF(f)
            cmd = fm.elidedText(cmd or "…", Qt.TextElideMode.ElideRight, max_width - 16)
            box = QRectF(x, y, fm.horizontalAdvance(cmd) + 16, 21)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("chip"))
            p.drawRoundedRect(box, 6, 6)
            text(p, x + 8, y + 15, cmd, f, color("text"))

        def buttons(self, p, x, y, items):
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
                x = text(p, x, y, label, font(12, QFont.Weight.Medium), color("text"), W - 16 - x) + 16

        def trail(self, p, s, phase):
            """The last few steps, like breadcrumbs: Read ✓ › Grep ✓ › Bash."""
            items = [(name, "done") for name in s.get("trail", [])]
            if phase == "working" and s.get("tool"):
                items.append((s["tool"]["name"], "now"))
            if not items:
                if phase == "thinking":
                    self.buttons(p, 52, 90, [("brake", "Stop")])
                return
            f = font(11.5, QFont.Weight.Medium)
            x = 52
            for i, (name, state) in enumerate(items[-4:]):
                if i:
                    x = text(p, x + 5, 90, "›", f, color("faint")) + 5
                label = tool_label(name) + (" ✓" if state == "done" else "")
                x = text(p, x, 90, label, f, color("coral" if state == "now" else "muted"))
            steps = s.get("steps", 0)
            if phase == "done" and steps:
                text(p, x + 12, 90, f"{steps} step{'s' if steps != 1 else ''}", f, color("faint"),
                     W - 16 - x - 12)

        def typing(self, p, s):
            t = s.get("type") or {}
            chars, idx = t.get("chars") or "?", t.get("idx", 0)
            showing = t.get("preview") is not None
            # The word so far, with the letter you're on boxed at the end.
            x = text(p, 52, 67, t.get("word", ""), font(17, QFont.Weight.DemiBold), color("text"),
                     180, Qt.TextElideMode.ElideLeft) + 2
            box = QRectF(x, 49, 21, 25)
            p.setPen(pen(color("coral"), 1.5))
            p.setBrush(color("coral") if showing else color("coral", 0.12))
            p.drawRoundedRect(box, 5, 5)
            p.setFont(font(16, QFont.Weight.Bold))
            p.setPen(QColor("#ffffff") if showing else color("coral"))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, chars[idx % len(chars)])

            # The letters either side, so you can see which way to turn the dial.
            cx = W - 70
            for k in (-2, -1, 0, 1, 2):
                ch = chars[(idx + k) % len(chars)]
                p.setFont(font(15 if k == 0 else 12.5, QFont.Weight.Bold if k == 0 else QFont.Weight.Medium))
                p.setPen(color("coral") if k == 0 else color("muted", 0.8 if abs(k) == 1 else 0.4))
                p.drawText(QRectF(cx + k * 17 - 8, 48, 16, 22), Qt.AlignmentFlag.AlignCenter, ch)
            p.setPen(pen(color("coral", 0.7), 1.4))
            p.drawLine(QPointF(cx - 5, 72), QPointF(cx + 5, 72))
            f = font(10.5)
            label = f"{t.get('set', '')} · dial"
            text(p, cx - QFontMetricsF(f).horizontalAdvance(label) / 2, 86, label, f, color("faint"))

            self.buttons(p, 52, 94, [("cross", "Space"), ("circle", "Delete")])

        def footer(self, p, s):
            p.setPen(pen(color("line"), 1))
            p.drawLine(QPointF(16, 106), QPointF(W - 16, 106))
            y = 128
            # Gear badge, which pops when you shift.
            gear = s.get("gear")
            pop = max(0.0, 1 - (time.monotonic() - self.gear_changed) / 0.3)
            size = 24 * (1 + 0.25 * pop)
            badge = QRectF(28 - size / 2, 119 - size / 2, size, size)
            p.setPen(Qt.PenStyle.NoPen if gear else pen(color("faint"), 1.2))
            p.setBrush(color("coral") if gear else Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(badge, 7, 7)
            p.setFont(font(13, QFont.Weight.Bold))
            p.setPen(QColor("#ffffff") if gear else color("faint"))
            p.drawText(badge, Qt.AlignmentFlag.AlignCenter, gear or "N")

            x = text(p, 50, y - 4, model_name(s.get("model")), font(13.5, QFont.Weight.DemiBold),
                     color("text"), 120)
            levels = s.get("effort_levels") or []
            if s.get("effort") in levels:
                level = levels.index(s["effort"])
                x += 10
                for i in range(len(levels)):   # effort as rising bars, like signal strength
                    h = 5 + i * 2.2
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(color("coral") if i <= level else color("line"))
                    p.drawRoundedRect(QRectF(x + i * 6, y - 4 - h, 4, h), 1.5, 1.5)
                text(p, x + len(levels) * 6 + 5, y - 4, s["effort"], font(11.5), color("muted"))

            # Mode on the right, then a little wheel that turns with yours.
            if not s.get("daemon"):
                mode, c = "Offline", "faint"
            elif not s.get("connected"):
                mode, c = "Wheel off", "faint"
            elif not s.get("armed"):
                mode, c = "Disarmed", "amber"
            else:
                mode, c = ("Type", "coral") if s.get("mode") == "type" else ("Drive", "muted")
            f = font(11.5, QFont.Weight.DemiBold)
            w = QFontMetricsF(f).horizontalAdvance(mode) + 16
            chip = QRectF(W - 16 - 22 - 10 - w, y - 19, w, 20)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("chip"))
            p.drawRoundedRect(chip, 10, 10)
            text(p, chip.left() + 8, y - 5, mode, f, color(c))

            p.save()
            p.translate(W - 16 - 11, y - 9)
            p.rotate(self.steer)
            rim = color("muted" if s.get("armed") else "faint")
            p.setPen(pen(rim, 2.2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(0, 0), 9.5, 9.5)
            p.setPen(pen(rim, 1.8))
            for a in (180, 0, 90):
                r = math.radians(a)
                p.drawLine(QPointF(math.cos(r) * 2.5, math.sin(r) * 2.5),
                           QPointF(math.cos(r) * 9, math.sin(r) * 9))
            p.setPen(pen(color("coral"), 2.4))
            p.drawLine(QPointF(0, -9.5), QPointF(0, -7))
            p.restore()

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
    hud = Hud(hud_cfg.get("scale", 1.0), hud_cfg.get("opacity", 0.96))

    if args.screenshot:
        hud.set_state(demo_state(args.at))
        hud.last_turn = 9.5   # the demo's turn, from prompt to done
        hud.gear_changed = 0.0
        hud.steer = hud.state["steer_deg"]
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

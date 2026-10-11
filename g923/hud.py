"""Floating HUD: a small racing-style dashboard in a corner of the screen.

F1-style rev lights show what Claude is doing, a scrolling input trace shows
your pedals and steering, and a dial in the corner turns with your wheel and
shows the gear, model and effort. In Type mode the trace makes way for the
letter picker. It never takes focus and clicks go straight through it, so the
wheel's key presses still reach Claude.

It watches g923d over the same socket the Claude Code hooks use. Needs the
optional Qt dependency: pipx install 'g923-claude[hud]'.
"""

import argparse
import json
import math
import os
import sys
import time
from collections import deque
from pathlib import Path

from . import config

W, H = 440, 150
FONT_DIR = Path(__file__).with_name("fonts")
COLORS = {
    "accent": "#ff5a36", "text": "#eef2f6", "dim": "#8a93a0", "faint": "#262c35",
    "green": "#22e06b", "red": "#ff2e45", "blue": "#3d7bff", "amber": "#ffb020",
    "panel_top": "#161a22", "panel_bottom": "#090b0f",
}
STATUS_COLORS = {"busy": "amber", "attention": "red", "done": "green"}
PEDAL_COLORS = {"clutch": "blue", "brake": "red", "gas": "green"}
LEDS = ["green"] * 5 + ["red"] * 5 + ["blue"] * 5   # an F1 wheel's shift lights
MODEL_NAMES = {"opus[1m]": "OPUS 1M", "opusplan": "OPUS PLAN"}
FAMILIES = ["Titillium Web", "Adwaita Sans", "Cantarell", "Segoe UI", "Helvetica Neue", "Arial"]
TRACE_SECONDS = 6.0
DONE_FOR = 4.0     # seconds the "done" lights stay up
NOTICE_FOR = 2.5   # seconds a notification line stays up
OFFLINE = {"daemon": False}


def model_name(model):
    """'opus' -> OPUS, 'claude-sonnet-5-5[1m]' -> SONNET 5.5 1M."""
    if not model:
        return "—"
    if model in MODEL_NAMES:
        return MODEL_NAMES[model]
    model, long_context = model.removesuffix("[1m]"), model.endswith("[1m]")
    parts = model.removeprefix("claude-").split("-")
    words = [p for p in parts if not p.isdigit()]
    version = ".".join(p for p in parts if p.isdigit() and len(p) <= 2)  # drop date stamps
    name = " ".join(words + [version] if version else words).upper()
    return name + (" 1M" if long_context else "")


def clock(seconds):
    seconds = max(0, int(seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


def lit_leds(claude, now, busy_since=None, done_at=None):
    """How the 15 rev lights look right now: (count lit, brightness)."""
    n = len(LEDS)
    if claude == "attention":
        return n, 1.0 if int(now * 4) % 2 else 0.15      # flashing, like a pit limiter
    if claude == "busy":
        phase = ((now - (busy_since or now)) % 1.8) / 1.8
        return round(n * (0.5 - 0.5 * math.cos(2 * math.pi * phase))), 1.0  # revving
    if done_at and now - done_at < DONE_FOR:
        return n, 1 - (now - done_at) / DONE_FOR
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
        "steer_deg": round(70 * math.sin(t * 0.7) + 15 * math.sin(t * 2.3), 1),
        "pedals": {
            "clutch": 0.0,
            "brake": round(max(0.0, -math.sin(t * 0.9 + 0.4)) ** 3, 2),
            "gas": round(max(0.0, math.sin(t * 0.9)) ** 0.7, 2),
        },
        "type": {"set": "abc", "chars": "abcdefghijklmnopqrstuvwxyz", "idx": 0,
                 "preview": None, "word": ""},
        "notice": None,
    }
    if 2 <= t < 4:
        s["pedals"]["clutch"] = 1.0
    if t >= 3:
        s["gear"], s["model"] = "3", "opus"
        s["notice"] = {"title": "Gear 3", "body": "/model opus", "at": now - (t - 3)}
    if 5 <= t < 14:
        s["claude"], s["busy_since"] = "busy", now - (t - 5)
    if 10 <= t < 12:
        s["claude"] = "attention"
    if t >= 14:
        s["done_at"] = now - (t - 14)
    if t >= 17:
        word = "refactor"[: int((t - 17) * 1.4)]
        s["mode"] = "type"
        s["type"].update(word=word, idx=s["type"]["chars"].index("refactor"[len(word) % 8]),
                         preview="refactor"[len(word) % 8] if int(t * 3) % 2 else None)
        s["notice"] = None
    return s


def build(args, cfg):
    # On GNOME/Wayland an app can't choose where its window goes or keep it
    # on top, so run through XWayland, where it can.
    if (sys.platform.startswith("linux") and os.environ.get("WAYLAND_DISPLAY")
            and os.environ.get("DISPLAY") and "QT_QPA_PLATFORM" not in os.environ):
        os.environ["QT_QPA_PLATFORM"] = "xcb"
    try:
        from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
        from PySide6.QtGui import (QColor, QFont, QFontDatabase, QFontMetricsF,
                                   QGuiApplication, QLinearGradient, QPainter,
                                   QPainterPath, QPen, QRadialGradient)
        from PySide6.QtNetwork import QLocalSocket
        from PySide6.QtWidgets import QApplication, QWidget
    except ImportError:
        sys.exit("g923-hud needs Qt: pipx install 'g923-claude[hud]' "
                 "(or pip install PySide6-Essentials)")

    hud_cfg = cfg.get("hud", {})

    def color(name, alpha=1.0):
        c = QColor(COLORS.get(name, name))
        c.setAlphaF(max(0.0, min(1.0, alpha)))
        return c

    def font(size, weight=QFont.Weight.Bold, spacing=0.0, italic=False, tabular=False):
        f = QFont()
        f.setFamilies(FAMILIES)
        f.setPixelSize(round(size))
        f.setWeight(weight)
        f.setItalic(italic)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        if tabular:
            f.setFeature(QFont.Tag("tnum"), 1)  # digits that don't jiggle as the timer runs
        return f

    def pen(c, width, cap=Qt.PenCapStyle.RoundCap):
        return QPen(c, width, Qt.PenStyle.SolidLine, cap, Qt.PenJoinStyle.RoundJoin)

    def glow(p, center, radius, c, strength):
        g = QRadialGradient(center, radius)
        g.setColorAt(0, color(c.name(), 0.55 * strength))
        g.setColorAt(1, color(c.name(), 0.0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(g)
        p.drawEllipse(center, radius, radius)

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
            self.pedals = {"clutch": 0.0, "brake": 0.0, "gas": 0.0}
            self.trace = deque(maxlen=int(TRACE_SECONDS * 30))
            self.busy_since = None
            self.last_turn = None   # how long the last turn took, for "done in"
            self.last_tick = self.last_sample = time.monotonic()
            self.setWindowTitle("g923 HUD")
            self.setWindowFlags(
                Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint
                | Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus
                | Qt.WindowType.WindowTransparentForInput
                | Qt.WindowType.X11BypassWindowManagerHint)
            self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
            self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self.setFixedSize(round(W * scale), round(H * scale))
            self.timer = QTimer(interval=16)
            self.timer.timeout.connect(self.tick)
            self.timer.start()

        def set_state(self, state):
            if state.get("busy_since"):
                self.busy_since = state["busy_since"]
            elif self.busy_since and state.get("done_at"):
                self.last_turn = state["done_at"] - self.busy_since
                self.busy_since = None
            self.state = state

        def sample(self, state):
            p = state.get("pedals", {})
            self.trace.append((p.get("gas", 0.0), p.get("brake", 0.0), p.get("clutch", 0.0),
                               state.get("steer_deg", 0.0)))

        def tick(self):
            now = time.monotonic()
            k = min(1.0, (now - self.last_tick) * 14)
            self.last_tick = now
            s = self.state
            self.steer += (max(-450, min(450, s.get("steer_deg", 0.0))) - self.steer) * k
            for name in self.pedals:
                target = s.get("pedals", {}).get(name, 0.0)
                self.pedals[name] += (target - self.pedals[name]) * k
            if now - self.last_sample >= 1 / 30:
                self.last_sample = now
                self.sample({"pedals": self.pedals, "steer_deg": self.steer})
            self.update()

        # --- painting -------------------------------------------------

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
            p.scale(self.scale, self.scale)
            s = self.state
            live = bool(s.get("daemon") and s.get("connected") and s.get("armed"))
            p.setOpacity(self.opacity)
            self.panel(p)
            p.setOpacity(self.opacity * (1.0 if live else 0.5))
            self.rev_lights(p, s, live)
            if s.get("mode") == "type" and live:
                self.letters(p, s)
            else:
                self.inputs(p, s)
            self.pedal_bars(p)
            self.dial(p, s, live)
            self.footer(p, s)
            p.end()

        def panel(self, p):
            # A slanted leading edge, like a racing livery.
            r, slant = 16, 26
            path = QPainterPath()
            path.moveTo(slant + r, 0)
            path.lineTo(W - r, 0)
            path.quadTo(W, 0, W, r)
            path.lineTo(W, H - r)
            path.quadTo(W, H, W - r, H)
            path.lineTo(r, H)
            path.quadTo(0, H, 2, H - r * 0.9)
            path.lineTo(slant - 2, r * 0.9)
            path.quadTo(slant, 0, slant + r, 0)
            g = QLinearGradient(0, 0, 0, H)
            g.setColorAt(0, color("panel_top", 0.93))
            g.setColorAt(1, color("panel_bottom", 0.95))
            p.fillPath(path, g)
            p.setPen(pen(QColor(255, 255, 255, 18), 1))
            p.drawPath(path)
            # Accent stripe running parallel to the slanted edge, just inside it.
            top, bottom = QPointF(slant - 2, r * 0.9), QPointF(2, H - r * 0.9)

            def inside(y):
                t = (y - top.y()) / (bottom.y() - top.y())
                return QPointF(top.x() + (bottom.x() - top.x()) * t + 9, y)

            p.setPen(pen(color("accent", 0.9), 3))
            p.drawLine(inside(28), inside(H - 28))

        def rev_lights(self, p, s, live):
            now = time.time()
            claude = s.get("claude", "idle") if live else "idle"
            lit, level = lit_leds(claude, now, s.get("busy_since"), s.get("done_at"))
            for i, name in enumerate(LEDS):
                c = QPointF(52 + i * 17.2, 18)
                if claude == "idle":
                    name = "green"  # finished: the whole strip goes green
                elif claude == "attention":
                    name = "red"
                on = i < lit and level > 0.05
                if on:
                    glow(p, c, 10, color(name), level)
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(name, 0.25 + 0.75 * level) if on else color("faint"))
                p.drawEllipse(c, 4.2, 4.2)

        def inputs(self, p, s):
            """RaceLab-style scrolling trace of the pedals and steering."""
            box = QRectF(40, 32, 232, 82)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRoundedRect(box, 8, 8)
            p.setPen(pen(QColor(255, 255, 255, 14), 1, Qt.PenCapStyle.FlatCap))
            for frac in (0.25, 0.5, 0.75):
                y = box.top() + box.height() * frac
                p.drawLine(QPointF(box.left() + 6, y), QPointF(box.right() - 6, y))

            samples = list(self.trace)
            if len(samples) < 2:
                return
            inner = box.adjusted(6, 8, -6, -8)
            step = inner.width() / (self.trace.maxlen - 1)
            x0 = inner.right() - step * (len(samples) - 1)

            def line(values, c, width, fill=False, alpha=1.0):
                path = QPainterPath()
                for i, v in enumerate(values):
                    pt = QPointF(x0 + i * step, inner.bottom() - inner.height() * max(0.0, min(1.0, v)))
                    path.lineTo(pt) if i else path.moveTo(pt)
                if fill:
                    area = QPainterPath(path)
                    area.lineTo(inner.right(), inner.bottom())
                    area.lineTo(x0, inner.bottom())
                    g = QLinearGradient(0, inner.top(), 0, inner.bottom())
                    g.setColorAt(0, color(c, 0.28))
                    g.setColorAt(1, color(c, 0.0))
                    p.fillPath(area, g)
                p.setPen(pen(color(c, alpha), width))
                p.setBrush(Qt.BrushStyle.NoBrush)
                p.drawPath(path)

            p.save()
            p.setClipRect(box)
            # Steering: centre line is straight ahead, edges are half a turn.
            line([0.5 - max(-1, min(1, x[3] / 180)) / 2 for x in samples], "text", 1.4, alpha=0.5)
            line([x[2] for x in samples], "blue", 1.4)
            line([x[1] for x in samples], "red", 2.0, fill=True)
            line([x[0] for x in samples], "green", 2.0, fill=True)
            p.restore()

        def letters(self, p, s):
            t = s.get("type") or {}
            chars, idx = t.get("chars") or "?", t.get("idx", 0)
            showing = t.get("preview") is not None
            box = QRectF(40, 32, 232, 82)
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(0, 0, 0, 70))
            p.drawRoundedRect(box, 8, 8)
            p.setFont(font(8.5, spacing=1.6))
            p.setPen(color("accent"))
            p.drawText(QPointF(box.left() + 9, box.top() + 13), f"TYPE · {t.get('set', '')}")

            cx, cy = box.center().x(), box.top() + 39
            for k in (-3, -2, -1, 1, 2, 3):
                ch = chars[(idx + k) % len(chars)]
                p.setFont(font(24 - 4 * abs(k), QFont.Weight.DemiBold))
                p.setPen(color("text", (0.75, 0.45, 0.2)[abs(k) - 1]))
                p.drawText(QRectF(cx + k * 31 - 15, cy - 18, 30, 36), Qt.AlignmentFlag.AlignCenter, ch)
            letter = QRectF(cx - 18, cy - 23, 36, 46)
            if showing:
                glow(p, letter.center(), 30, color("accent"), 0.7)
            p.setPen(pen(color("accent"), 2))
            p.setBrush(color("accent") if showing else color("accent", 0.12))
            p.drawRoundedRect(letter, 9, 9)
            p.setFont(font(30, italic=True))
            p.setPen(color("panel_bottom") if showing else color("accent"))
            p.drawText(letter, Qt.AlignmentFlag.AlignCenter, chars[idx % len(chars)])

            f = font(12, QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(color("text", 0.9))
            word = QFontMetricsF(f).elidedText(t.get("word", "") + "▏", Qt.TextElideMode.ElideLeft, 200)
            p.drawText(QRectF(box.left(), box.bottom() - 17, box.width(), 14), Qt.AlignmentFlag.AlignCenter, word)

        def pedal_bars(self, p):
            for i, name in enumerate(("clutch", "brake", "gas")):
                x, top, h = 280 + i * 9, 34, 78
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color("faint"))
                p.drawRoundedRect(QRectF(x, top, 5, h), 2.5, 2.5)
                amount = max(0.0, min(1.0, self.pedals[name]))
                if amount > 0.02:
                    p.setBrush(color(PEDAL_COLORS[name]))
                    p.drawRoundedRect(QRectF(x, top + h * (1 - amount), 5, h * amount), 2.5, 2.5)

        def dial(self, p, s, live):
            """The corner dial: a rim that turns with your wheel, gear in the middle,
            effort as a tach arc around it."""
            cx, cy, r = 368, 70, 44
            now = time.time()
            claude = s.get("claude") if live else None
            if claude == "attention":
                glow(p, QPointF(cx, cy), r + 22, color("red"), 0.6 + 0.4 * math.sin(now * 8))
            elif live and s.get("done_at") and now - s["done_at"] < DONE_FOR:
                glow(p, QPointF(cx, cy), r + 22, color("green"), 1 - (now - s["done_at"]) / DONE_FOR)

            # Effort, as a tachometer arc with a red zone at the top end.
            levels = s.get("effort_levels") or []
            if s.get("effort") in levels:
                level, n = levels.index(s["effort"]), len(levels)
                span, gap = 240 / n, 5
                for i in range(n):
                    start = 210 - i * span - gap / 2
                    on = i <= level
                    c = color("red" if i == n - 1 else "accent") if on else color("faint")
                    p.setPen(pen(c, 3.5, Qt.PenCapStyle.FlatCap))
                    p.drawArc(QRectF(cx - r - 9, cy - r - 9, 2 * (r + 9), 2 * (r + 9)),
                              round(start * 16), round(-(span - gap) * 16))

            # The rim turns with the real wheel.
            p.save()
            p.translate(cx, cy)
            p.rotate(self.steer)
            g = QLinearGradient(0, -r, 0, r)
            g.setColorAt(0, QColor("#4a525e"))
            g.setColorAt(1, QColor("#1c2128"))
            p.setPen(QPen(g, 8))
            p.setBrush(QColor(0, 0, 0, 90))
            p.drawEllipse(QPointF(0, 0), r - 4, r - 4)
            p.setPen(pen(QColor("#3a414c"), 4))
            for side in (-1, 1):
                p.drawLine(QPointF(side * (r - 8), 0), QPointF(side * (r - 15), 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("accent"))
            p.drawRoundedRect(QRectF(-3, -r - 1, 6, 10), 2, 2)
            p.restore()

            gear = s.get("gear")
            p.setFont(font(44, italic=True))
            p.setPen(color("accent") if gear else color("dim", 0.35))
            p.drawText(QRectF(cx - 30, cy - 30, 60, 56), Qt.AlignmentFlag.AlignCenter, gear or "N")
            if s.get("effort"):
                p.setFont(font(8.5, spacing=1.2))
                p.setPen(color("dim"))
                p.drawText(QRectF(cx - 30, cy + 18, 60, 12), Qt.AlignmentFlag.AlignCenter, s["effort"].upper())

            f = font(15, italic=True, spacing=0.6)
            p.setFont(f)
            p.setPen(color("text"))
            name = QFontMetricsF(f).elidedText(model_name(s.get("model")), Qt.TextElideMode.ElideRight, 120)
            p.drawText(QRectF(cx - 62, H - 30, 124, 20), Qt.AlignmentFlag.AlignCenter, name)

        def footer(self, p, s):
            now = time.time()
            if not s.get("daemon"):
                text, col = "NO DAEMON", "dim"
            elif not s.get("connected"):
                text, col = "WHEEL OFF", "dim"
            elif not s.get("armed"):
                text, col = "DISARMED · PRESS PS", "dim"
            elif s.get("claude") == "attention":
                text, col = "NEEDS YOU", "red"
            elif s.get("claude") == "busy":
                text, col = "WORKING  " + clock(now - (s.get("busy_since") or now)), "amber"
            elif s.get("done_at") and now - s["done_at"] < DONE_FOR:
                took = f"  {clock(self.last_turn)}" if self.last_turn else ""
                text, col = "DONE" + took, "green"
            else:
                text, col = "READY", "dim"
            x, y = 44, H - 15
            if col != "dim":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(col))
                p.drawEllipse(QPointF(x + 3, y - 4), 3, 3)
                x += 12
            f = font(11, spacing=1.4, tabular=True)
            p.setFont(f)
            p.setPen(color(col))
            p.drawText(QPointF(x, y), text)
            x += QFontMetricsF(f).horizontalAdvance(text) + 14

            n = s.get("notice")
            if n and s.get("mode") != "type" and now - n.get("at", 0) < NOTICE_FOR:
                age = now - n["at"]
                p.save()
                p.setOpacity(p.opacity() * min(1.0, (NOTICE_FOR - age) / 0.6))
                f = font(11, QFont.Weight.DemiBold)
                p.setFont(f)
                p.setPen(color("red" if n["title"].startswith("Grind") else "text", 0.85))
                body = f"  ·  {n['body']}" if n.get("body") else ""
                text = QFontMetricsF(f).elidedText(n["title"] + body, Qt.TextElideMode.ElideRight, max(0, 300 - x))
                p.drawText(QPointF(x, y), text)
                p.restore()

    def place(widget):
        screens = QGuiApplication.screens()
        want = hud_cfg.get("screen", "primary")
        screen = next((sc for sc in screens if sc.name() == want), None) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        m = hud_cfg.get("margin", 24)
        corner = hud_cfg.get("corner", "bottom-right")
        x = geo.left() + m if "left" in corner else geo.right() - widget.width() - m + 1
        y = geo.top() + m if "top" in corner else geo.bottom() - widget.height() - m + 1
        widget.move(x, y)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("g923-hud")
    for ttf in sorted(FONT_DIR.glob("*.ttf")):
        QFontDatabase.addApplicationFont(str(ttf))
    hud = Hud(hud_cfg.get("scale", 1.0), hud_cfg.get("opacity", 0.94))

    if args.screenshot:
        for k in range(hud.trace.maxlen, 0, -1):   # fill the trace with the seconds before
            hud.sample(demo_state(args.at - k / 30))
        hud.set_state(demo_state(args.at))
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

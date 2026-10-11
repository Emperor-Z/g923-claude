"""Floating HUD: a small racing-style dashboard in a corner of the screen.

Shows the gear and model, whether Claude is working, the steering angle, the
pedals and effort level, and in Type mode the letters around the one you're
picking. It never takes focus and clicks go straight through it, so the
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

from . import config

W, H = 360, 128
ACCENT = "#ff5a36"
COLORS = {
    "text": "#e6edf3", "dim": "#8b949e", "faint": "#3a404a",
    "busy": "#ffb347", "attention": "#ff4d5e", "done": "#3ddc97",
    "clutch": "#7aa7ff", "brake": "#ff6b81", "gas": "#4fd1c5",
}
MODEL_NAMES = {"opus[1m]": "OPUS 1M", "opusplan": "OPUS PLAN"}
SANS = ["Inter", "Adwaita Sans", "Cantarell", "Segoe UI", "Helvetica Neue", "Arial"]
MONO = ["JetBrains Mono", "Adwaita Mono", "Cascadia Mono", "Menlo", "Consolas", "monospace"]
DONE_FOR = 4.0     # seconds the "done" glow stays up
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


def demo_state(t):
    """A scripted ~24 s loop through everything the HUD can show."""
    t %= 24
    now = time.time()
    s = {
        "daemon": True, "connected": True, "armed": True, "mode": "drive",
        "claude": "idle", "busy_since": None, "done_at": None,
        "gear": None, "model": "sonnet", "effort": "high",
        "effort_levels": ["low", "medium", "high", "xhigh", "max"],
        "steer_deg": round(60 * math.sin(t * 1.3), 1),
        "pedals": {"clutch": 0.0, "brake": 0.0, "gas": 0.0},
        "type": {"set": "abc", "chars": "abcdefghijklmnopqrstuvwxyz", "idx": 0,
                 "preview": None, "word": ""},
        "notice": None,
    }
    if 2 <= t < 4:
        s["pedals"]["clutch"] = 1.0
    if t >= 3:
        s["gear"], s["model"] = "3", "opus"
        s["notice"] = {"title": "Gear 3", "body": "/model opus", "at": now - (t - 3)}
    if 4.5 <= t < 5:
        s["pedals"]["gas"] = 1.0
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
        from PySide6.QtGui import (QColor, QConicalGradient, QFont, QFontMetricsF,
                                   QGuiApplication, QLinearGradient, QPainter,
                                   QPainterPath, QPen)
        from PySide6.QtNetwork import QLocalSocket
        from PySide6.QtWidgets import QApplication, QWidget
    except ImportError:
        sys.exit("g923-hud needs Qt: pipx install 'g923-claude[hud]' "
                 "(or pip install PySide6-Essentials)")

    hud_cfg = cfg.get("hud", {})

    def color(name, alpha=1.0):
        c = QColor(COLORS.get(name, name))
        c.setAlphaF(max(0.0, min(1.0, c.alphaF() * alpha)))
        return c

    def font(families, size, weight=QFont.Weight.Normal, spacing=0.0):
        f = QFont()
        f.setFamilies(families)
        f.setPixelSize(round(size))
        f.setWeight(weight)
        if spacing:
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, spacing)
        return f

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
            self.busy_since = None
            self.last_turn = None   # how long the last turn took, for "done in"
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

        def tick(self):
            now = time.monotonic()
            k = min(1.0, (now - self.last_tick) * 14)
            self.last_tick = now
            s = self.state
            self.steer += (max(-450, min(450, s.get("steer_deg", 0.0))) - self.steer) * k
            for name in self.pedals:
                target = s.get("pedals", {}).get(name, 0.0)
                self.pedals[name] += (target - self.pedals[name]) * k
            self.update()

        # --- painting -------------------------------------------------

        def paintEvent(self, _):
            p = QPainter(self)
            p.setRenderHints(QPainter.RenderHint.Antialiasing | QPainter.RenderHint.TextAntialiasing)
            p.scale(self.scale, self.scale)
            s = self.state
            live = s.get("daemon") and s.get("connected") and s.get("armed")
            p.setOpacity(self.opacity)
            self.panel(p)
            p.setOpacity(self.opacity * (1.0 if live else 0.55))
            self.wheel(p, s)
            if s.get("mode") == "type" and live:
                self.letters(p, s)
            else:
                self.gear(p, s)
            self.status(p, s)
            self.pedal_bars(p)
            self.notice(p, s)
            p.end()

        def panel(self, p):
            path = QPainterPath()
            path.addRoundedRect(QRectF(0.5, 0.5, W - 1, H - 1), 22, 22)
            g = QLinearGradient(0, 0, 0, H)
            g.setColorAt(0, QColor(24, 28, 36, 235))
            g.setColorAt(1, QColor(10, 12, 17, 235))
            p.fillPath(path, g)
            p.setPen(QPen(QColor(255, 255, 255, 22), 1))
            p.drawPath(path)

        def wheel(self, p, s):
            cx, cy, r = 64, H / 2, 38
            now = time.time()
            ring = QRectF(cx - r - 12, cy - r - 12, 2 * (r + 12), 2 * (r + 12))
            claude = s.get("claude", "idle") if s.get("daemon") else "off"
            p.setPen(QPen(color("faint", 0.6), 3))
            p.drawEllipse(ring)
            if claude == "busy":
                g = QConicalGradient(QPointF(cx, cy), -now * 300 % 360)
                g.setColorAt(0.0, color("busy"))
                g.setColorAt(0.35, color("busy", 0.0))
                g.setColorAt(1.0, color("busy", 0.0))
                p.setPen(QPen(g, 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                p.drawEllipse(ring)
            elif claude == "attention":
                pulse = 0.55 + 0.45 * math.sin(now * 7)
                p.setPen(QPen(color("attention", pulse), 5))
                p.drawEllipse(ring)
            elif s.get("done_at") and now - s["done_at"] < DONE_FOR:
                fade = 1 - (now - s["done_at"]) / DONE_FOR
                p.setPen(QPen(color("done", fade), 4))
                p.drawEllipse(ring)

            p.save()
            p.translate(cx, cy)
            p.rotate(self.steer)
            rim = color("dim" if s.get("armed") else "faint")
            p.setPen(QPen(rim, 7))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(0, 0), r - 6, r - 6)
            p.setPen(QPen(rim, 6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            for angle in (180, 0, 90):
                a = math.radians(angle)
                p.drawLine(QPointF(math.cos(a) * 11, math.sin(a) * 11),
                           QPointF(math.cos(a) * (r - 8), math.sin(a) * (r - 8)))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(color("faint"))
            p.drawEllipse(QPointF(0, 0), 13, 13)
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(QRectF(-3.5, -r - 1, 7, 12), 2.5, 2.5)
            p.drawEllipse(QPointF(0, 0), 3.5, 3.5)
            p.restore()

        def status(self, p, s):
            now = time.time()
            x = 132
            if not s.get("daemon"):
                text, col = "NO DAEMON", "dim"
            elif not s.get("connected"):
                text, col = "WHEEL OFF", "dim"
            elif not s.get("armed"):
                text, col = "DISARMED · PRESS PS", "dim"
            elif s.get("claude") == "attention":
                text, col = "NEEDS YOU", "attention"
            elif s.get("claude") == "busy":
                text = "WORKING " + clock(now - (s.get("busy_since") or now))
                col = "busy"
            elif s.get("done_at") and now - s["done_at"] < DONE_FOR:
                took = f" IN {clock(self.last_turn)}" if self.last_turn else ""
                text, col = "DONE ✓" + took, "done"
            else:
                text, col = "READY", "dim"
            if s.get("mode") == "type" and s.get("daemon"):
                # The big gear readout is busy showing letters, so keep it up here.
                text += f"  ·  {s.get('gear') or 'N'} {model_name(s.get('model'))}"
            if col != "dim":
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color(col))
                p.drawEllipse(QPointF(x + 3.5, 22), 3.5, 3.5)
                x += 13
            p.setFont(font(SANS, 11, QFont.Weight.Bold, 1.6))
            p.setPen(color(col))
            p.drawText(QPointF(x, 26), text)

            mode = (s.get("mode") or "drive").upper() if s.get("daemon") else "—"
            f = font(SANS, 9, QFont.Weight.Bold, 1.4)
            w = QFontMetricsF(f).horizontalAdvance(mode) + 14
            pill = QRectF(W - 16 - w, 14, w, 17)
            p.setPen(QPen(QColor(ACCENT if mode == "TYPE" else COLORS["faint"]), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRoundedRect(pill, 8.5, 8.5)
            p.setFont(f)
            p.setPen(QColor(ACCENT) if mode == "TYPE" else color("dim"))
            p.drawText(pill, Qt.AlignmentFlag.AlignCenter, mode)

        def gear(self, p, s):
            gear = s.get("gear")
            p.setFont(font(MONO, 50, QFont.Weight.Bold))
            p.setPen(QColor(ACCENT) if gear else color("faint"))
            p.drawText(QRectF(126, 30, 50, 62), Qt.AlignmentFlag.AlignCenter, gear or "N")

            p.setFont(font(SANS, 18, QFont.Weight.Bold, 1.0))
            p.setPen(color("text"))
            name = QFontMetricsF(p.font()).elidedText(
                model_name(s.get("model")), Qt.TextElideMode.ElideRight, 110)
            p.drawText(QPointF(182, 58), name)

            levels = s.get("effort_levels") or []
            if s.get("effort") in levels:
                level = levels.index(s["effort"])
                for i in range(len(levels)):
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(ACCENT) if i <= level else color("faint"))
                    p.drawRoundedRect(QRectF(183 + i * 12, 68, 9, 5), 2, 2)
                p.setFont(font(SANS, 10, QFont.Weight.DemiBold))
                p.setPen(color("dim"))
                p.drawText(QPointF(183 + len(levels) * 12 + 4, 74), s["effort"])

        def letters(self, p, s):
            t = s.get("type") or {}
            chars, idx = t.get("chars") or "?", t.get("idx", 0)
            showing = t.get("preview") is not None
            cx, cy = 208, 60
            for k in (-2, -1, 1, 2):
                ch = chars[(idx + k) % len(chars)]
                p.setFont(font(MONO, 20 - 3 * abs(k), QFont.Weight.DemiBold))
                p.setPen(color("dim", 0.85 if abs(k) == 1 else 0.4))
                p.drawText(QRectF(cx + k * 30 - 14, cy - 16, 28, 32), Qt.AlignmentFlag.AlignCenter, ch)
            box = QRectF(cx - 17, cy - 21, 34, 42)
            p.setPen(QPen(QColor(ACCENT), 2))
            p.setBrush(QColor(ACCENT) if showing else QColor(255, 90, 54, 30))
            p.drawRoundedRect(box, 9, 9)
            p.setFont(font(MONO, 26, QFont.Weight.Bold))
            p.setPen(QColor("#0b0e13") if showing else QColor(ACCENT))
            p.drawText(box, Qt.AlignmentFlag.AlignCenter, chars[idx % len(chars)])

            p.setFont(font(SANS, 9, QFont.Weight.Bold, 1.2))
            p.setPen(color("dim"))
            p.drawText(QPointF(132, 98), t.get("set") or "")
            f = font(MONO, 13)
            p.setFont(f)
            p.setPen(color("text"))
            word = QFontMetricsF(f).elidedText(t.get("word", "") + "▏", Qt.TextElideMode.ElideLeft, 110)
            p.drawText(QPointF(170, 99), word)

        def pedal_bars(self, p):
            for i, name in enumerate(("clutch", "brake", "gas")):
                x, top, h = W - 60 + i * 15, 40, 56
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(color("faint", 0.7))
                p.drawRoundedRect(QRectF(x, top, 8, h), 4, 4)
                amount = max(0.0, min(1.0, self.pedals[name]))
                if amount > 0.02:
                    p.setBrush(color(name))
                    p.drawRoundedRect(QRectF(x, top + h * (1 - amount), 8, h * amount), 4, 4)
                p.setFont(font(SANS, 8, QFont.Weight.Bold))
                p.setPen(color("dim", 0.8))
                p.drawText(QRectF(x - 4, top + h + 3, 16, 12), Qt.AlignmentFlag.AlignCenter, name[0].upper())

        def notice(self, p, s):
            n = s.get("notice")
            if not n or s.get("mode") == "type":
                return
            age = time.time() - n.get("at", 0)
            if age > NOTICE_FOR:
                return
            p.setOpacity(p.opacity() * min(1.0, (NOTICE_FOR - age) / 0.6))
            text = n["title"] + (f"  ·  {n['body']}" if n.get("body") else "")
            f = font(SANS, 11, QFont.Weight.DemiBold)
            p.setFont(f)
            p.setPen(color("attention" if n["title"].startswith("Grind") else "text"))
            p.drawText(QPointF(132, 108), QFontMetricsF(f).elidedText(text, Qt.TextElideMode.ElideRight, 150))

    def place(widget):
        screens = QGuiApplication.screens()
        want = hud_cfg.get("screen", "primary")
        screen = next((sc for sc in screens if sc.name() == want), None) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        m = hud_cfg.get("margin", 24)
        corner = hud_cfg.get("corner", "bottom-left")
        x = geo.left() + m if "left" in corner else geo.right() - widget.width() - m + 1
        y = geo.top() + m if "top" in corner else geo.bottom() - widget.height() - m + 1
        widget.move(x, y)

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("g923-hud")
    hud = Hud(hud_cfg.get("scale", 1.0), hud_cfg.get("opacity", 0.92))

    if args.screenshot:
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

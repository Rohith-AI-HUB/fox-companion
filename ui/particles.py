import math, random
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QPainterPath
from core import config

class Particle:
    def __init__(self, x, y, vx, vy, lifetime, size, color, kind="circle"):
        self.x = x
        self.y = y
        self.vx = vx
        self.vy = vy
        self.lifetime = lifetime
        self.max_lifetime = lifetime
        self.size = size
        self.color = color
        self.kind = kind
        self.text = ""
        self.rotation = random.uniform(0, 360)

    def update(self, dt):
        self.x += self.vx * dt
        self.y += self.vy * dt
        self.vy += 200 * dt
        self.lifetime -= dt
        self.rotation += dt * 180
        alive = self.lifetime > 0
        if self.kind in ("zzz",):
            self.vy -= 30 * dt
            self.vx += math.sin(self.rotation * 0.1) * 5 * dt
        return alive

    def alpha(self):
        t = max(0, self.lifetime / self.max_lifetime)
        if t > 0.8:
            return int(255 * (1 - (t - 0.8) / 0.2))
        return int(255 * t)

class ParticleManager(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.particles = []
        self._dust_timer = 0
        self._zzz_timer = 0
        self._anchor_win = None
        self.resize(1920, 1080)
        self.show()

    def set_anchor_window(self, win):
        self._anchor_win = win

    def spawn_dust(self, x, y, count=3):
        for _ in range(count):
            px = x + random.uniform(-4, 4)
            py = y + random.uniform(-2, 2)
            vx = random.uniform(-15, -5) if random.random() < 0.5 else random.uniform(5, 15)
            vy = random.uniform(-30, -10)
            size = random.uniform(1.5, 3.5)
            alpha = random.randint(60, 120)
            c = QColor(180, 140, 100, alpha)
            self.particles.append(Particle(px, py, vx, vy, config.DUST_LIFETIME, size, c))

    def spawn_landing_puff(self, x, y):
        for _ in range(config.LANDING_PUFF_COUNT):
            angle = random.uniform(0, math.pi * 2)
            speed = random.uniform(20, 60)
            px = x + random.uniform(-6, 6)
            py = y
            vx = math.cos(angle) * speed
            vy = math.sin(angle) * speed - 20
            size = random.uniform(2, 5)
            alpha = random.randint(80, 160)
            c = QColor(200, 160, 110, alpha)
            self.particles.append(Particle(px, py, vx, vy, 0.4, size, c))

    def spawn_bonk_stars(self, x, y):
        for _ in range(config.BONK_STAR_COUNT):
            px = x + random.uniform(-8, 8)
            py = y + random.uniform(-8, 8)
            vx = random.uniform(-40, 40)
            vy = random.uniform(-60, -20)
            c = QColor(255, 220, 50, 200)
            self.particles.append(Particle(px, py, vx, vy, config.BONK_STAR_LIFETIME, 4, c, kind="star"))

    def spawn_zzz(self, x, y):
        px = x + random.uniform(-4, 4)
        py = y + random.uniform(-4, 4)
        vx = random.uniform(-3, 3)
        vy = -15
        c = QColor(120, 130, 180, 180)
        p = Particle(px, py, vx, vy, config.Zzz_LIFETIME, 10, c, kind="zzz")
        p.text = "z"
        self.particles.append(p)

    def spawn_heart(self, x, y):
        for _ in range(config.HEART_COUNT):
            px = x + random.uniform(-6, 6)
            py = y + random.uniform(-6, 6)
            vx = random.uniform(-15, 15)
            vy = random.uniform(-40, -10)
            c = QColor(220, 60, 80, 200)
            self.particles.append(Particle(px, py, vx, vy, config.HEART_LIFETIME, 6, c, kind="heart"))

    def update_particles(self, dt):
        if self._anchor_win:
            self.move(self._anchor_win.pos().x() - 100, self._anchor_win.pos().y() - 200)
            self.resize(300, 300)
        self.particles = [p for p in self.particles if p.update(dt)]
        if self.particles:
            self.update()

    def paintEvent(self, event):
        if not self.particles:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for p in self.particles:
            a = p.alpha()
            if a <= 0:
                continue
            c = QColor(p.color)
            c.setAlpha(a)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(c)
            if p.kind == "circle":
                painter.drawEllipse(QPointF(p.x, p.y), p.size, p.size)
            elif p.kind == "star":
                painter.save()
                painter.translate(p.x, p.y)
                painter.rotate(p.rotation)
                pts = []
                for i in range(5):
                    angle = i * 2 * math.pi / 5 - math.pi / 2
                    pts.append(QPointF(math.cos(angle) * p.size, math.sin(angle) * p.size))
                    angle += math.pi / 5
                    pts.append(QPointF(math.cos(angle) * p.size * 0.4, math.sin(angle) * p.size * 0.4))
                poly = [QPointF(px, py) for px, py in [(pt.x(), pt.y()) for pt in pts]]
                painter.drawPolygon(poly)
                painter.restore()
            elif p.kind == "zzz":
                f = QFont("Courier New", int(p.size))
                painter.setFont(f)
                painter.setPen(QPen(c, 1))
                painter.drawText(QRectF(p.x - 10, p.y - 10, 20, 20), Qt.AlignmentFlag.AlignCenter, p.text)
            elif p.kind == "heart":
                painter.save()
                painter.translate(p.x, p.y)
                s = p.size
                path = QPainterPath()
                path.moveTo(0, s * 0.3)
                path.cubicTo(-s, -s * 0.3, -s, s * 0.7, 0, s)
                path.cubicTo(s, s * 0.7, s, -s * 0.3, 0, s * 0.3)
                painter.drawPath(path)
                painter.restore()
        painter.end()

import random
import time
from PyQt6.QtWidgets import QWidget, QMenu
from PyQt6.QtCore import Qt, QPoint
from PyQt6.QtGui import QPixmap, QMouseEvent, QAction, QPainter
from core import config

class CompanionWindow(QWidget):
    open_chat = None  # callable, set by main

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setMouseTracking(True)
        self.resize(config.WINDOW_SIZE, config.WINDOW_SIZE)
        self.dragging = False
        self.drag_offset = QPoint()
        self.click_through = False
        self._pixmap = None
        self._hovered = False
        self._direction = 1
        self._transform_scale = 1.0
        self._transform_squash = 1.0
        self._breath_time = 0.0
        self._drag_positions = []
        self._drag_times = []
        self._release_vx = 0.0
        self._release_vy = 0.0
        self._just_released = False

    def set_frame(self, path: str, direction: int = 1):
        self._pixmap = QPixmap(path)
        self._direction = direction
        self.update()

    def set_sprites(self, sprites):
        self.sprites = sprites

    def set_behavior(self, behavior):
        self.behavior = behavior

    def set_physics(self, physics):
        self.physics = physics

    def paintEvent(self, e):
        if not self._pixmap:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        if self.dragging:
            painter.setOpacity(config.DRAG_OPACITY)

        pw = self._pixmap.width()
        ph = self._pixmap.height()
        if pw == 0 or ph == 0:
            painter.end()
            return
        scale = self._transform_scale * self._transform_squash
        sw = float(config.WINDOW_SIZE) * scale * (1.0 if self._direction > 0 else -1.0)
        sh = float(config.WINDOW_SIZE) * scale
        painter.translate(config.WINDOW_SIZE / 2, config.WINDOW_SIZE / 2)
        painter.scale(sw / pw, sh / ph)
        painter.drawPixmap(-pw // 2, -ph // 2, self._pixmap)
        painter.end()

    def enterEvent(self, e):
        self._hovered = True
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        super().enterEvent(e)

    def leaveEvent(self, e):
        self._hovered = False
        self.setCursor(Qt.CursorShape.ArrowCursor)
        super().leaveEvent(e)

    def mousePressEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            self.dragging = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self.drag_offset = e.globalPosition().toPoint() - self.pos()
            self._drag_positions = [(e.globalPosition().toPoint(), self._now())]
            self._drag_times = [self._now()]

    def mouseMoveEvent(self, e: QMouseEvent):
        if self.dragging:
            self.move(e.globalPosition().toPoint() - self.drag_offset)
            now = self._now()
            self._drag_positions.append((e.globalPosition().toPoint(), now))
            if len(self._drag_positions) > 4:
                self._drag_positions.pop(0)
            self._drag_times.append(now)
            if len(self._drag_times) > 4:
                self._drag_times.pop(0)

    def mouseReleaseEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            dragged = len(self._drag_positions) >= 2 and (
                (self._drag_positions[-1][0] - self._drag_positions[0][0]).manhattanLength() > 10
            )
            self.dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            if not dragged and self.open_chat:
                self.open_chat()
                return
            if len(self._drag_positions) >= 2:
                p0, t0 = self._drag_positions[0]
                p1, t1 = self._drag_positions[-1]
                dt = t1 - t0
                if dt > 0:
                    vx = (p1.x() - p0.x()) / dt
                    vy = (p1.y() - p0.y()) / dt
                else:
                    vx = vy = 0.0
            else:
                vx = vy = 0.0
            self._release_vx = vx
            self._release_vy = vy
            self._just_released = True

    def _now(self):
        return time.perf_counter()

    def set_voice_bubble(self, voice, bubble):
        self.voice = voice
        self.bubble = bubble

    def mouseDoubleClickEvent(self, e: QMouseEvent):
        if e.button() == Qt.MouseButton.LeftButton:
            action = random.choice(["hit", "jump"])
            self.sprites.play(action, loop=False)
            self.sprites.on_finish = lambda: self.sprites.play("idle")
            if hasattr(self, 'physics'):
                dir = 1 if random.random() < 0.5 else -1
                self.physics.jump(config.DOUBLE_CLICK_JUMP_VY, dir * config.DOUBLE_CLICK_JUMP_VX)
            if hasattr(self, 'particles') and random.random() < 0.4:
                self.particles.spawn_heart(self.x() + self.width() // 2, self.y() + self.height() // 2)
            if hasattr(self, 'voice') and self.voice and self.bubble and random.random() < 0.6:
                from core.dialogue import get_line
                line = get_line("poke_reaction")
                if line:
                    self.bubble.show_text(line, self.x() + self.width() // 2, self.y() + config.MOUTH_Y)
                    self.voice.speak(line)

    def toggle_click_through(self):
        self.click_through = not self.click_through
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, self.click_through)
        return self.click_through

    def on_action(self, action: str):
        action = action.lower()
        self.behavior.set_continuous_walk(False, action)
        if action == "walk":
            self.behavior.set_continuous_walk(True, action)
        elif action == "sit":
            self.sprites.play("sit", loop=False)
            self.sprites.on_finish = lambda: self.sprites.play("sit_idle")
        elif action in ("hit", "jump"):
            self.sprites.play(action, loop=False)
            self.sprites.on_finish = lambda: self.sprites.play("idle")
        else:
            self.sprites.play(action)
        self._say_manual(action)

    def _say_manual(self, action: str):
        if not hasattr(self, 'voice') or not self.voice or not self.bubble:
            return
        from core.dialogue import get_line
        key = "manual_" + action
        line = get_line(key)
        if not line:
            return
        self.bubble.show_text(line, self.x() + self.width() // 2, self.y() + config.MOUTH_Y)
        self.voice.speak(line)

    def contextMenuEvent(self, e):
        menu = QMenu(self)
        menu.setStyleSheet(f"""
            QMenu {{
                background-color: {config.BUBBLE_BG_HEX};
                border: 2px solid {config.BUBBLE_BORDER_HEX};
                border-radius: 4px;
                padding: 4px;
                color: {config.BUBBLE_TEXT_HEX};
            }}
            QMenu::item {{
                padding: 6px 24px 6px 12px;
                border-radius: 2px;
            }}
            QMenu::item:selected {{
                background-color: {config.BUBBLE_BORDER_HEX};
                color: {config.BUBBLE_BG_HEX};
            }}
            QMenu::separator {{
                height: 1px;
                background-color: {config.BUBBLE_BORDER_HEX};
                margin: 2px 6px;
            }}
        """)
        for action in ["Idle", "Walk", "Sit", "Jump", "Hit"]:
            act = QAction(action, self)
            act.triggered.connect(lambda _, a=action: self.on_action(a))
            menu.addAction(act)
        menu.exec(e.globalPos())

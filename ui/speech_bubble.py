import math, itertools
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QTextOption, QBrush, QPen, QPolygonF, QPainterPath
from core import config


class SpeechBubble(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.text = ""
        self.hide()
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._hide_timer = QTimer()
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self._fade_out)
        self._wrap_width = config.BUBBLE_WRAP_WIDTH
        self._is_night = False

        self._thinking = False
        self._thinking_dot_count = 0
        self._thinking_bounce = 0.0
        self._thinking_timer = QTimer()
        self._thinking_timer.setInterval(config.THINKING_DOTS_INTERVAL)
        self._thinking_timer.timeout.connect(self._advance_thinking)

    def set_night_mode(self, night: bool):
        self._is_night = night
        self.update()

    def _colors(self):
        if self._is_night:
            return (
                QColor(*config.BUBBLE_BG_NIGHT),
                QColor(*config.BUBBLE_BORDER_NIGHT),
                QColor(*config.BUBBLE_TEXT_COLOR_NIGHT),
                QColor(*config.BUBBLE_SHADOW_COLOR),
            )
        return (
            QColor(*config.BUBBLE_BG),
            QColor(*config.BUBBLE_BORDER),
            QColor(*config.BUBBLE_TEXT_COLOR),
            QColor(*config.BUBBLE_SHADOW_COLOR),
        )

    def show_thinking(self, anchor_x: int, anchor_y: int):
        self._hide_timer.stop()
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._thinking = True
        self._thinking_dot_count = 0
        self._thinking_bounce = 0.0
        self.text = "."
        self._thinking_timer.start()
        self._update_size(anchor_x, anchor_y)
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_anim.stop()
        self._fade_anim.setDuration(config.BUBBLE_FADE_IN_MS)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()

    def hide_thinking(self):
        self._thinking = False
        self._thinking_timer.stop()
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._fade_anim.stop()
        self._fade_anim.setDuration(config.BUBBLE_FADE_OUT_MS)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self.hide)
        self._fade_anim.start()

    def _advance_thinking(self):
        self._thinking_dot_count = (self._thinking_dot_count + 1) % 4
        self.text = "." * (self._thinking_dot_count + 1)
        t = self._thinking_dot_count / 4.0
        self._thinking_bounce = math.sin(t * config.THINKING_BOUNCE_FREQ * math.pi) * config.THINKING_BOUNCE_AMP
        self.update()

    def _update_size(self, anchor_x, anchor_y):
        font = QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE)
        metrics = QFontMetrics(font)
        padding = config.BUBBLE_PADDING
        line_w = metrics.horizontalAdvance(self.text)
        w = min(line_w + padding * 2, self._wrap_width)
        h = metrics.height() + padding * 2
        if line_w > self._wrap_width - padding * 2:
            lines = max(1, line_w // (self._wrap_width - padding * 2) + 1)
            h = metrics.height() * lines + padding * 2
            w = self._wrap_width
        sx, sy = config.BUBBLE_SHADOW_OFFSET
        self.resize(w + abs(sx) + config.BUBBLE_SHADOW_BLUR * 2,
                    h + abs(sy) + config.BUBBLE_SHADOW_BLUR * 2 + config.BUBBLE_TAIL_H)
        self.move(anchor_x - self.width() // 2, anchor_y - self.height())

    def show_text(self, text: str, anchor_x: int, anchor_y: int,
                  duration_ms: int = 3000):
        self._hide_timer.stop()
        self._thinking = False
        self._thinking_timer.stop()
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self.text = text
        self._update_size(anchor_x, anchor_y)
        self._thinking_bounce = 0.0
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_anim.stop()
        self._fade_anim.setDuration(config.BUBBLE_FADE_IN_MS)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        if duration_ms > 0:
            self._hide_timer.start(duration_ms)

    def _fade_out(self):
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self._fade_anim.stop()
        self._fade_anim.setDuration(config.BUBBLE_FADE_OUT_MS)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self.hide)
        self._fade_anim.start()

    def paintEvent(self, event):
        if not self.text:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        bw, bh = r.width(), r.height()
        tail_w, tail_h = config.BUBBLE_TAIL_W, config.BUBBLE_TAIL_H
        radius = config.BUBBLE_RADIUS
        blur = config.BUBBLE_SHADOW_BLUR
        sx, sy = config.BUBBLE_SHADOW_OFFSET

        bg_color, border_color, text_color, shadow_color = self._colors()

        # Build rounded body rect (leave room for tail + shadow)
        body = r.adjusted(blur, blur, -blur, -tail_h - blur)

        # Apply thinking bounce offset
        bounce_y = int(self._thinking_bounce * body.height())

        # Draw soft shadow first
        if blur > 0:
            painter.setBrush(QBrush(shadow_color))
            painter.setPen(Qt.PenStyle.NoPen)
            for i in range(blur, 0, -1):
                alpha = int(shadow_color.alpha() * (1 - i / (blur + 1)))
                c = QColor(shadow_color.red(), shadow_color.green(), shadow_color.blue(), alpha)
                painter.setBrush(QBrush(c))
                shadow_rect = body.adjusted(sx - i, sy - i, sx + i, sy + i).translated(0, bounce_y)
                path = QPainterPath()
                path.addRoundedRect(QRectF(shadow_rect), radius, radius)
                painter.drawPath(path)

        # Draw body with rounded corners
        body_path = QPainterPath()
        body_path.addRoundedRect(QRectF(body.translated(0, bounce_y)), radius, radius)

        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1))
        painter.drawPath(body_path)

        # Draw tail (speech pointer)
        tx = bw // 2 - tail_w // 2
        ty = bh - tail_h - blur + bounce_y
        tail = QPolygonF([
            QPointF(float(tx), float(ty)),
            QPointF(float(tx + tail_w), float(ty)),
            QPointF(float(bw // 2), float(bh - blur + bounce_y)),
        ])
        painter.setBrush(QBrush(bg_color))
        painter.setPen(QPen(border_color, 1))
        painter.drawPolygon(tail)

        # Draw text
        painter.setPen(text_color)
        font = QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE)
        painter.setFont(font)
        option = QTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignCenter)
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        text_rect = body.adjusted(6, 4, -6, -4).translated(0, bounce_y)
        painter.drawText(QRectF(text_rect), self.text, option)
        painter.end()

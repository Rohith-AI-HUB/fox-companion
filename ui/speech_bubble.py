from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QRectF, QPointF
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QTextOption, QBrush, QPen, QPolygonF
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

    def show_text(self, text: str, anchor_x: int, anchor_y: int,
                  duration_ms: int = 3000):
        self._hide_timer.stop()
        try:
            self._fade_anim.finished.disconnect()
        except TypeError:
            pass
        self.text = text
        font = QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE)
        metrics = QFontMetrics(font)
        padding = config.BUBBLE_PADDING
        line_w = metrics.horizontalAdvance(text)
        w = min(line_w + padding * 2, self._wrap_width)
        h = metrics.height() + padding * 2
        if line_w > self._wrap_width - padding * 2:
            lines = max(1, line_w // (self._wrap_width - padding * 2) + 1)
            h = metrics.height() * lines + padding * 2
            w = self._wrap_width
        self.resize(w, h)
        self.move(anchor_x - w // 2, anchor_y - h)
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
        r = self.rect()
        bw, bh = r.width(), r.height()
        tail_w, tail_h = config.BUBBLE_TAIL_W, config.BUBBLE_TAIL_H

        body = r.adjusted(0, 0, 0, -tail_h)

        painter.setBrush(QBrush(QColor(*config.BUBBLE_BG)))
        painter.setPen(QPen(QColor(*config.BUBBLE_BORDER), 1))
        painter.drawRect(body.adjusted(0, 0, 0, 0))

        tx = bw // 2 - tail_w // 2
        ty = bh - tail_h
        tail = QPolygonF([
            QPointF(float(tx), float(ty)),
            QPointF(float(tx + tail_w), float(ty)),
            QPointF(float(bw // 2), float(bh)),
        ])
        painter.setBrush(QBrush(QColor(*config.BUBBLE_BG)))
        painter.setPen(QPen(QColor(*config.BUBBLE_BORDER), 1))
        painter.drawPolygon(tail)

        painter.setPen(QColor(*config.BUBBLE_TEXT_COLOR))
        font = QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE)
        painter.setFont(font)
        option = QTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignCenter)
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        painter.drawText(QRectF(body.adjusted(4, 0, -4, 0)), self.text, option)
        painter.end()

import math, itertools
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QRectF, QPointF
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QTextOption, QBrush, QPen,
    QPolygonF, QPainterPath, QPixmap,
)
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

        # ── Render cache: skip shadow+body recomputation every frame ──
        # Cache a fully-rendered pixmap keyed by static inputs. The
        # per-frame ``thinking_bounce`` translation is applied on blit,
        # so only a single off-screen paint is done per bubble change.
        self._cache_key: tuple | None = None
        self._cache_pixmap: QPixmap | None = None

    def _static_cache_key(self) -> tuple:
        return (
            self.text,
            self.width(),
            self.height(),
            self.devicePixelRatioF(),
            self._is_night,
            self._wrap_width,
            int(config.BUBBLE_PADDING),
            int(config.BUBBLE_RADIUS),
            int(config.BUBBLE_TAIL_W),
            int(config.BUBBLE_TAIL_H),
        )

    def _render_to_pixmap(self) -> QPixmap:
        """Render bubble body + shadow + tail + text into an off-screen QPixmap.

        The pixmap is created at the widget's device-pixel ratio so the cached
        render stays sharp on HiDPI / DPI-scaled displays; a logical-resolution
        cache would be upscaled by the compositor and blur the text.
        """
        dpr = max(1.0, self.devicePixelRatioF())
        pm = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pm.setDevicePixelRatio(dpr)
        pm.fill(Qt.GlobalColor.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        r = pm.rect()
        bw, bh = r.width(), r.height()
        tail_w, tail_h = config.BUBBLE_TAIL_W, config.BUBBLE_TAIL_H
        radius = config.BUBBLE_RADIUS
        blur = config.BUBBLE_SHADOW_BLUR
        sx, sy = config.BUBBLE_SHADOW_OFFSET

        bg_color, border_color, text_color, shadow_color = self._colors()

        body = r.adjusted(blur, blur, -blur, -tail_h - blur)

        # ── Soft shadow (single precomputed draw, not per-frame) ──
        if blur > 0:
            p.setBrush(QBrush(shadow_color))
            p.setPen(Qt.PenStyle.NoPen)
            for i in range(blur, 0, -1):
                alpha = int(shadow_color.alpha() * (1 - i / (blur + 1)))
                c = QColor(shadow_color.red(), shadow_color.green(), shadow_color.blue(), alpha)
                p.setBrush(QBrush(c))
                shadow_rect = body.adjusted(sx - i, sy - i, sx + i, sy + i)
                path = QPainterPath()
                path.addRoundedRect(QRectF(shadow_rect), radius, radius)
                p.drawPath(path)

        # ── Body ──
        body_path = QPainterPath()
        body_path.addRoundedRect(QRectF(body), radius, radius)
        p.setBrush(QBrush(bg_color))
        p.setPen(QPen(border_color, 1))
        p.drawPath(body_path)

        # ── Tail ──
        tx = bw // 2 - tail_w // 2
        ty = bh - tail_h - blur
        tail = QPolygonF([
            QPointF(float(tx), float(ty)),
            QPointF(float(tx + tail_w), float(ty)),
            QPointF(float(bw // 2), float(bh - blur)),
        ])
        p.setBrush(QBrush(bg_color))
        p.setPen(QPen(border_color, 1))
        p.drawPolygon(tail)

        # ── Text ──
        p.setPen(text_color)
        font = QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE)
        p.setFont(font)
        option = QTextOption()
        option.setAlignment(Qt.AlignmentFlag.AlignCenter)
        option.setWrapMode(QTextOption.WrapMode.WordWrap)
        text_rect = body.adjusted(6, 4, -6, -4)
        p.drawText(QRectF(text_rect), self.text, option)
        p.end()
        return pm

    def set_night_mode(self, night: bool):
        self._is_night = night
        self._cache_key = None
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
        self._cache_key = None
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
        self._cache_key = None
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
        self._cache_key = None
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
        self._cache_key = None

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
        self._cache_key = None
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
        key = self._static_cache_key()
        if self._cache_key != key or self._cache_pixmap is None:
            self._cache_pixmap = self._render_to_pixmap()
            self._cache_key = key

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
        bounce_y = int(self._thinking_bounce * (self.height() - config.BUBBLE_TAIL_H - config.BUBBLE_SHADOW_BLUR * 2))
        painter.drawPixmap(0, bounce_y, self._cache_pixmap)
        painter.end()

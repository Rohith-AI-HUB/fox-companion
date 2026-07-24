import os
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QRectF
from PyQt6.QtGui import QPainter, QColor, QFont, QFontMetrics, QTextOption
from core import config

ONBOARDING_FILE = ".onboarding_done"

class OnboardingHints(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self._hints = []
        self._hint_index = 0
        self._fade_anim = QPropertyAnimation(self, b"windowOpacity")
        self._timer = QTimer()
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._next_hint)
        self._anchor_win = None
        self.hide()

    def start(self, anchor_win):
        if os.path.exists(ONBOARDING_FILE):
            return
        self._anchor_win = anchor_win
        self._hints = [
            ("Drag me!", "Grab and toss me around"),
            ("Double-click!", "I jump when you double-click"),
            ("Talk to me!", "Press Ctrl+Alt+F or tray menu"),
        ]
        self._hint_index = 0
        self._show_current()

    def _show_current(self):
        if self._hint_index >= len(self._hints):
            self._fade_out_and_close()
            return
        title, sub = self._hints[self._hint_index]
        self.resize(240, 60)
        win = self._anchor_win
        if win:
            self.move(win.x() + win.width() + 20, win.y())
        self._title = title
        self._sub = sub
        self.setWindowOpacity(0.0)
        self.show()
        self._fade_anim.stop()
        self._fade_anim.setDuration(config.ONBOARDING_FADE_IN_MS)
        self._fade_anim.setStartValue(0.0)
        self._fade_anim.setEndValue(1.0)
        self._fade_anim.start()
        self._timer.start(config.ONBOARDING_HINT_DURATION_MS)

    def _next_hint(self):
        self._hint_index += 1
        self._show_current()

    def _fade_out_and_close(self):
        self._fade_anim.stop()
        self._fade_anim.setDuration(config.ONBOARDING_FADE_OUT_MS)
        self._fade_anim.setStartValue(self.windowOpacity())
        self._fade_anim.setEndValue(0.0)
        self._fade_anim.finished.connect(self._done)
        self._fade_anim.start()

    def _done(self):
        self.hide()
        try:
            with open(ONBOARDING_FILE, "w") as f:
                f.write("done")
        except Exception:
            pass

    def paintEvent(self, event):
        if not self._hints:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = self.rect()
        painter.setBrush(QColor(50, 40, 30, 220))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(r, 8, 8)
        painter.setPen(QColor(240, 230, 210))
        f = QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE + 2)
        painter.setFont(f)
        painter.drawText(QRectF(r.adjusted(10, 6, -10, -24)), self._title, QTextOption(Qt.AlignmentFlag.AlignCenter))
        f2 = QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE - 1)
        painter.setFont(f2)
        painter.setPen(QColor(180, 170, 150))
        painter.drawText(QRectF(r.adjusted(10, 26, -10, -6)), self._sub, QTextOption(Qt.AlignmentFlag.AlignCenter))
        painter.end()

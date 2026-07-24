from PyQt6.QtWidgets import QWidget, QLineEdit, QVBoxLayout
from PyQt6.QtCore import Qt, pyqtSignal, QRectF
from PyQt6.QtGui import QFont, QPainter, QColor, QPen
from core import config

class ChatInput(QWidget):
    submitted = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)

        self._anchor_x = 0
        self._anchor_y = 0

        self.edit = QLineEdit(self)
        self.edit.setFont(QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE))
        self.edit.setPlaceholderText(config.CHAT_PLACEHOLDER)
        self.edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {config.BUBBLE_BG_HEX};
                border: 2px solid {config.BUBBLE_BORDER_HEX};
                border-radius: 4px;
                color: {config.BUBBLE_TEXT_HEX};
                padding: 6px 8px;
                selection-background-color: {config.BUBBLE_BORDER_HEX};
            }}
            QLineEdit::placeholder {{
                color: {config.BUBBLE_BORDER_HEX};
                opacity: 128;
            }}
        """)
        self.edit.returnPressed.connect(self._on_submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)

        self.resize(config.CHAT_INPUT_WIDTH, config.CHAT_INPUT_HEIGHT)
        self.hide()

    def open_at(self, x: int, y: int):
        from PyQt6.QtWidgets import QApplication
        screen = QApplication.primaryScreen().availableGeometry()
        w, h = self.width(), self.height()
        x = max(screen.x(), min(x, screen.x() + screen.width() - w))
        y = max(screen.y(), min(y, screen.y() + screen.height() - h))
        self._anchor_x = x + w // 2
        self._anchor_y = y + h
        self.move(x, y)
        self.edit.clear()
        self.show()
        self.edit.setFocus()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = config.CHAT_TETHER_COLOR
        painter.setPen(QPen(QColor(*c), config.CHAT_TETHER_WIDTH))
        cx = self.width() // 2
        painter.drawLine(cx, self.height(), self._anchor_x - self.x(), self._anchor_y - self.y())

    def _on_submit(self):
        text = self.edit.text().strip()
        self.hide()
        if text:
            self.submitted.emit(text)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide()
        else:
            super().keyPressEvent(event)

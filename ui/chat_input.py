from PyQt6.QtWidgets import QWidget, QLineEdit, QVBoxLayout
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
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

        self.edit = QLineEdit(self)
        self.edit.setFont(QFont(config.BUBBLE_FONT_FAMILY, config.BUBBLE_FONT_SIZE))
        self.edit.setStyleSheet(f"""
            QLineEdit {{
                background-color: {config.BUBBLE_BG_HEX};
                border: 2px solid {config.BUBBLE_BORDER_HEX};
                border-radius: 0px;
                color: {config.BUBBLE_TEXT_HEX};
                padding: 6px 8px;
                selection-background-color: {config.BUBBLE_BORDER_HEX};
            }}
        """)
        self.edit.returnPressed.connect(self._on_submit)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.edit)

        self.resize(220, 32)
        self.hide()

    def open_at(self, x: int, y: int):
        self.move(x, y)
        self.edit.clear()
        self.show()
        self.edit.setFocus()

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

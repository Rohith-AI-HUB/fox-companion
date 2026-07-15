from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter
from core import config

class RoadStrip(QWidget):
    def __init__(self, screen_width, tile_path=None):
        super().__init__()
        if tile_path is None:
            tile_path = config.ROAD_TILE_PATH
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowFlag(Qt.WindowType.WindowTransparentForInput, True)
        self.tile = QPixmap(tile_path)
        self.resize(screen_width, config.ROAD_HEIGHT)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawTiledPixmap(self.rect(), self.tile)
        painter.end()

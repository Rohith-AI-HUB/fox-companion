import os
from collections import OrderedDict
from PyQt6.QtGui import QPixmap

_PIXMAP_CACHE_MAX = 64


class _PixmapCache:
    """Bounded LRU cache mapping file path -> QPixmap.

    Fox sprites are ~10 KB each so a 64-entry cache fits in ~640 KB and
    fully covers all animation frames across every action.
    """

    def __init__(self, capacity: int = _PIXMAP_CACHE_MAX):
        self._data: "OrderedDict[str, QPixmap]" = OrderedDict()
        self._capacity = capacity

    def get(self, path: str) -> QPixmap:
        if path in self._data:
            self._data.move_to_end(path)
            return self._data[path]
        pm = QPixmap(path)
        self._data[path] = pm
        if len(self._data) > self._capacity:
            self._data.popitem(last=False)
        return pm


class SpriteManager:
    def __init__(self, window, assets_dir="assets/fox"):
        self.window = window
        self.assets_dir = assets_dir
        self.frames: list[str] = []
        self.idx = 0
        self.direction = 1
        self.fps = 8
        self.loop = True
        self.on_finish = None
        self._frame_accum = 0.0
        self._pixmap_cache = _PixmapCache()
        self._frame_counts: dict[str, int] = {}
        self._discover_all_actions()

    def _discover_all_actions(self):
        """Pre-scan assets_dir once so _count(action) is a dict lookup."""
        try:
            files = os.listdir(self.assets_dir)
        except OSError:
            return
        counts: dict[str, int] = {}
        for f in files:
            if not f.endswith(".png"):
                continue
            base = f[:-4]
            if "_" not in base:
                continue
            head, tail = base.rsplit("_", 1)
            if not tail.isdigit():
                continue
            n = int(tail)
            prev = counts.get(head, 0)
            if n > prev:
                counts[head] = n
        self._frame_counts = counts

    def play(self, action: str, fps: int = 8, loop: bool = True):
        self.frames = sorted(
            f"{self.assets_dir}/{action}_{i}.png"
            for i in range(1, self._count(action) + 1)
        )
        self.idx = 0
        self.fps = fps
        self.loop = loop
        self.on_finish = None
        self._frame_accum = 0.0

    def _count(self, action):
        if action in self._frame_counts:
            return self._frame_counts[action]
        prefix = f"{action}_"
        try:
            listing = os.listdir(self.assets_dir)
        except OSError:
            return 0
        n = 0
        for f in listing:
            if f.startswith(prefix) and f[len(prefix):-4].isdigit():
                n += 1
        self._frame_counts[action] = n
        return n

    def advance(self, dt):
        if not self.frames:
            return
        self._frame_accum += dt
        interval = 1.0 / self.fps
        while self._frame_accum >= interval:
            self._frame_accum -= interval
            path = self.frames[self.idx]
            pm = self._pixmap_cache.get(path)
            self.window.set_frame_pixmap(pm, self.direction)
            self.idx = (self.idx + 1) % len(self.frames)
            if self.idx == 0 and not self.loop:
                self.frames = []
                self._frame_accum = 0.0
                if self.on_finish:
                    self.on_finish()
                return

    def current_frame_path(self):
        return self.frames[self.idx] if self.frames else None

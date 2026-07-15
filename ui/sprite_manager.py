import os

class SpriteManager:
    def __init__(self, window, assets_dir="assets/fox"):
        self.window = window
        self.assets_dir = assets_dir
        self.frames = []
        self.idx = 0
        self.direction = 1
        self.fps = 8
        self.loop = True
        self.on_finish = None
        self._frame_accum = 0.0

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
        prefix = f"{action}_"
        return len([f for f in os.listdir(self.assets_dir)
                     if f.startswith(prefix) and f[len(prefix):-4].isdigit()])

    def advance(self, dt):
        if not self.frames:
            return
        self._frame_accum += dt
        interval = 1.0 / self.fps
        while self._frame_accum >= interval:
            self._frame_accum -= interval
            self.window.set_frame(self.frames[self.idx], self.direction)
            self.idx = (self.idx + 1) % len(self.frames)
            if self.idx == 0 and not self.loop:
                self.frames = []
                self._frame_accum = 0.0
                if self.on_finish:
                    self.on_finish()
                return

    def current_frame_path(self):
        return self.frames[self.idx] if self.frames else None

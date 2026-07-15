import sys
from core import config
from core.logger import get_logger

# NOTE: This screen watcher uses Windows-only APIs (ctypes.windll)
# It will not work on macOS or Linux
log = get_logger("screen_watcher")

if sys.platform.startswith("win"):
    from ctypes import Structure, windll, c_uint, byref
    import pygetwindow as gw
else:
    log.warning("Screen watcher is not supported on this platform (only Windows is supported)")
    windll = None
    gw = None

if sys.platform.startswith("win"):
    class LASTINPUTINFO(Structure):
        _fields_ = [("cbSize", c_uint), ("dwTime", c_uint)]

    CATEGORY_KEYWORDS = {
        "coding": [
            "visual studio", "code", "pycharm", "intellij", "sublime",
            "vim", "emacs", "android studio", "xcode", "eclipse",
            ".py", ".js", ".ts", ".java", ".cpp", ".cs", ".rs", ".go",
            "terminal", "cmd", "powershell", "git bash", "wsl",
            "notepad++", "nano", "neovim", "trae",
        ],
        "browsing": [
            "chrome", "firefox", "edge", "brave", "opera", "browser",
            "chromium", "tor browser", "vivaldi", "zen",
        ],
        "communication": [
            "discord", "slack", "teams", "zoom", "outlook", "telegram",
            "whatsapp", "signal", "messenger",
        ],
        "gaming": [
            "game", "minecraft", "steam", "epic games",
        ],
    }

    class ScreenWatcher:
        def __init__(self):
            self.active_title = ""
            self.prev_title = ""
            self.category = "other"
            self.prev_category = "other"
            self.idle_seconds = 0.0
            self._was_idle = False
            self.transition = None

        def poll(self):
            self.prev_title = self.active_title
            self.prev_category = self.category
            was_idle = self._was_idle

            lii = LASTINPUTINFO()
            lii.cbSize = c_uint(8)
            windll.user32.GetLastInputInfo(byref(lii))
            ticks_now = windll.kernel32.GetTickCount()
            self.idle_seconds = (ticks_now - lii.dwTime) / 1000.0
            self._was_idle = self.idle_seconds >= config.IDLE_AFK_SECONDS

            try:
                win = gw.getActiveWindow()
                self.active_title = win.title if win else ""
            except Exception:
                self.active_title = ""

            self.category = self._categorize(self.active_title)

            if was_idle and not self._was_idle:
                self.transition = "became_active"
            elif not was_idle and self._was_idle:
                self.transition = "became_idle"
            elif self.category != self.prev_category:
                self.transition = "category_changed"
            else:
                self.transition = None

            if self.category != self.prev_category or self.transition:
                log.debug("title=%s category=%s idle=%.0fs trans=%s", self.active_title, self.category, self.idle_seconds, self.transition)

        def _categorize(self, title: str) -> str:
            low = title.lower()
            for cat, keywords in CATEGORY_KEYWORDS.items():
                for kw in keywords:
                    if kw in low:
                        return cat
            return "other"

        def is_idle(self) -> bool:
            return self.idle_seconds >= config.IDLE_AFK_SECONDS

        def is_napping(self) -> bool:
            return self.idle_seconds >= config.IDLE_NAP_SECONDS
else:
    class ScreenWatcher:
        def __init__(self):
            self.active_title = ""
            self.prev_title = ""
            self.category = "other"
            self.prev_category = "other"
            self.idle_seconds = 0.0
            self._was_idle = False
            self.transition = None

        def poll(self):
            pass

        def is_idle(self) -> bool:
            return False

        def is_napping(self) -> bool:
            return False

"""Every-N-second screen reader + memory updater.

The fox periodically captures the primary screen via Qt, runs cheap coarse
change-detection on the GUI thread (ignores the cursor and micro-flash), and
only when the screen meaningfully changes sends the frame to a vision model
to summarise what the user is doing. New observations are stored in
long-term memory; the previous observation is superseded.

Screenshots exist only in memory — they are never written to disk. If the
primary vision provider (Groq) is unavailable it falls back to OpenAI's
vision API (the models behind ChatGPT) when ``OPENAI_API_KEY`` is configured.
"""

import base64
import io
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import numpy as np

from core import config
from core.logger import get_logger

log = get_logger("screen_reader")

_PROMPT = (
    "You are watching the user's screen through a screenshot. In ONE short, "
    "general sentence describe what the user is currently doing (which kind of "
    "app and which task). Ignore the taskbar, desktop widgets and decorative UI. "
    "Do not repeat exact on-screen text; keep it general — for example 'editing "
    "code in an IDE', 'browsing the web', 'writing a document'. If you cannot "
    "tell, say 'using the computer'."
)


class ScreenReader:
    """Grab the screen every few seconds and fold observations into memory."""

    def __init__(self, brain, on_observe=None):
        self.brain = brain
        self.on_observe = on_observe
        self._active = True
        self._last_sig = None
        self._last_observe = None
        self._last_vision_t = 0.0
        self._exec = ThreadPoolExecutor(max_workers=1, thread_name_prefix="fox-screen")

        self._groq = None
        if config.GROQ_API_KEY:
            try:
                from groq import Groq
                self._groq = Groq(api_key=config.GROQ_API_KEY)
            except Exception as e:
                log.warning("screen reader: groq not available: %s", e)

        # Optional ChatGPT-family fallback for screenshots.
        self._openai = None
        if config.OPENAI_API_KEY:
            try:
                from openai import OpenAI
                self._openai = OpenAI(api_key=config.OPENAI_API_KEY)
            except Exception as e:
                log.warning("screen reader: openai fallback unavailable: %s", e)

    def shutdown(self):
        self._active = False
        try:
            self._exec.shutdown(wait=False)
        except Exception:
            pass

    # ── GUI-thread tick ─────────────────────────────────────────────

    def poll(self):
        """Call on the GUI thread every ``SCREEN_READ_INTERVAL_S``."""
        if not self._active or not self.brain:
            return
        try:
            from PyQt6.QtWidgets import QApplication
            screen = QApplication.primaryScreen()
            if screen is None:
                return
            pixmap = screen.grabWindow(0)
        except Exception as e:
            log.warning("screen grab failed: %s", e)
            return

        img = self._to_pil(pixmap)
        if img is None:
            return

        sig = self._coarse_signature(img)
        if self._last_sig is None:
            self._last_sig = sig
            return
        diff = int(np.abs(self._last_sig.astype(int) - sig.astype(int)).sum())
        self._last_sig = sig
        if diff <= config.SCREEN_SIG_THRESHOLD:
            return  # no meaningful change (cursor/flicker ignored)

        now = time.monotonic()
        if now - self._last_vision_t < config.SCREEN_VISION_MIN_INTERVAL_S:
            return
        self._last_vision_t = now
        # Vision API call runs off the GUI thread; the PIL image is a plain
        # numpy-backed buffer, so it is safe to hand across threads.
        self._exec.submit(self._observe, img)

    # ── Worker thread ───────────────────────────────────────────────

    def _observe(self, img):
        if not self._active:
            return
        png = self._png_bytes(img)
        summary = self._ask_groq(png) if self._groq else None
        if not summary and self._openai is not None:
            summary = self._ask_openai(png)
        if not summary:
            log.info("no screen summary this cycle")
            return
        if self._similar(summary, self._last_observe):
            log.debug("screen summary unchanged — skipping memory write")
            return
        self._last_observe = summary

        if self.on_observe is not None:
            try:
                self.on_observe(summary)
            except Exception:
                pass

        if self.brain is not None:
            fact = f"{config.SCREEN_SOURCE_LABEL}: {summary}"
            self.brain.capture_async(
                fact, user_id="default",
                on_done=lambda _: log.info("learned from screen: %s", summary),
            )
        log.info("screen observation: %s", summary)

    # ── Vision providers ────────────────────────────────────────────

    def _ask_groq(self, png):
        try:
            url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            resp = self._groq.chat.completions.create(
                model=config.SCREEN_VISION_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }],
                max_tokens=config.SCREEN_VISION_MAX_TOKENS,
                temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            return text or None
        except Exception as e:
            log.warning("Groq vision failed: %s", e)
            return None

    def _ask_openai(self, png):
        try:
            url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
            resp = self._openai.chat.completions.create(
                model=config.SCREEN_OPENAI_VISION_MODEL,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": _PROMPT},
                        {"type": "image_url", "image_url": {"url": url}},
                    ],
                }],
                max_tokens=config.SCREEN_VISION_MAX_TOKENS,
                temperature=0.3,
            )
            text = (resp.choices[0].message.content or "").strip()
            return text or None
        except Exception as e:
            log.warning("OpenAI vision fallback failed: %s", e)
            return None

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _coarse_signature(img):
        """Downscale to a tiny grayscale grid; cursor/one-pixel flicker are
        filtered out because they barely move coarse block averages."""
        grid = config.SCREEN_POLL_COARSE
        small = img.convert("L").resize(grid)
        return np.frombuffer(small.tobytes(), dtype=np.uint8)

    @staticmethod
    def _png_bytes(img):
        buf = io.BytesIO()
        img.convert("RGB").save(buf, format="PNG")
        return buf.getvalue()

    @staticmethod
    def _to_pil(pixmap):
        from PyQt6.QtGui import QImage
        from PIL import Image
        img = pixmap.toImage().convertToFormat(QImage.Format.Format_RGB888)
        w, h = img.width(), img.height()
        if w <= 0 or h <= 0:
            return None
        bp = img.bytesPerLine()
        ptr = img.constBits()
        ptr.setsize(bp * h)
        rgb = np.frombuffer(ptr, dtype=np.uint8).reshape(h, bp)[:, : w * 3]
        rgb = np.ascontiguousarray(rgb).reshape(h, w, 3)
        return Image.fromarray(rgb, mode="RGB")

    @staticmethod
    def _similar(a, b):
        if not b:
            return False
        return SequenceMatcher(None, a.lower(), b.lower()).ratio() >= config.SCREEN_SIMILAR_THRESHOLD
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
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from difflib import SequenceMatcher

import numpy as np

from core import config
from core.logger import get_logger

log = get_logger("screen_reader")

_PROMPT = (
    "You are watching the user's screen through a screenshot. Reply with exactly "
    "ONE short, specific sentence describing what the user is currently doing. "
    "Include the concrete details you can actually read: the app name (e.g. VS "
    "Code, Chrome, a terminal), the visible file name, project, or tab title, the "
    "code language if code is on screen, and the action (editing, debugging, "
    "browsing, writing, messaging). Example: 'editing main.py (Python) in the "
    "fox-companion project in VS Code'. Do not use thinking, reasoning, lists, "
    "markdown, or any formatting; output only the plain sentence. If you cannot "
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
        self._cooldown_until = 0.0
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

    @property
    def latest_observation(self) -> str:
        """The most recent vision summary of the user's screen ('' if none yet)."""
        return self._last_observe or ""

    # ── GUI-thread tick ─────────────────────────────────────────────

    def poll(self):
        """Call on the GUI thread every ``SCREEN_READ_INTERVAL_S``.

        The cheap coarse signature is computed straight from the Qt pixmap
        (no full-resolution PIL conversion) so the sub-second detection
        cadence stays light on the GUI thread; the heavier PIL conversion
        is deferred to the rare moment a vision call is actually needed.
        """
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

        sig = self._coarse_signature_pixmap(pixmap)
        if sig is None:
            return
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
        if now < self._cooldown_until:
            return
        self._last_vision_t = now
        img = self._to_pil(pixmap)
        if img is None:
            return
        # Vision API call runs off the GUI thread; the PIL image is a plain
        # numpy-backed buffer, so it is safe to hand across threads.
        self._exec.submit(self._observe, img)

    # ── Worker thread ───────────────────────────────────────────────

    def _observe(self, img):
        if not self._active:
            return
        if time.monotonic() < self._cooldown_until:
            log.info("no screen summary this cycle (vision in rate-limit cooldown)")
            return
        png = self._png_bytes(img)
        # Prefer OpenAI (paid, more generous quota) over Groq's free tier.
        summary = self._ask_openai(png) if self._openai is not None else None
        if not summary and self._groq is not None:
            summary = self._ask_groq(png)
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

    def _enter_cooldown(self, err):
        """Pause all vision calls after a rate-limit so we stop hammering the API."""
        try:
            is_429 = getattr(err, "status_code", None) == 429 or "rate_limit" in str(err).lower()
        except Exception:
            is_429 = False
        if is_429:
            self._cooldown_until = time.monotonic() + config.SCREEN_VISION_RATELIMIT_COOLDOWN_S
            log.warning("vision rate-limited — pausing screen reading for %.0fs",
                        config.SCREEN_VISION_RATELIMIT_COOLDOWN_S)
        return is_429

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
                # qwen/qwen3.6-27b is a reasoning model; disable thinking so the
                # response is a plain sentence instead of a <think>…</think> block.
                reasoning_effort="none",
            )
            text = (resp.choices[0].message.content or "").strip()
            return self._clean_summary(text) or None
        except Exception as e:
            if not self._enter_cooldown(e):
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
            return self._clean_summary(text) or None
        except Exception as e:
            if not self._enter_cooldown(e):
                log.warning("OpenAI vision fallback failed: %s", e)
            return None

    # ── Helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _clean_summary(text: str) -> str:
        """Normalise vision-model output into one plain sentence for memory.

        Strips any leftover reasoning blocks (defensive; ``reasoning_effort``
        handles Groq), markdown list markers and line breaks.
        """
        if not text:
            return ""
        if "<think>" in text:
            end = text.find("</think>")
            text = text[end + len("</think>"):] if end != -1 else text.split("<think>", 1)[0]
        lines = [re.sub(r"^\s*(?:[-*]|\d+\.)\s*", "", ln) for ln in text.splitlines()]
        return " ".join(ln.strip() for ln in lines if ln.strip()).strip()

    @staticmethod
    def _coarse_signature(img):
        """Downscale to a tiny grayscale grid; cursor/one-pixel flicker are
        filtered out because they barely move coarse block averages."""
        grid = config.SCREEN_POLL_COARSE
        small = img.convert("L").resize(grid)
        return np.frombuffer(small.tobytes(), dtype=np.uint8)

    @staticmethod
    def _coarse_signature_pixmap(pixmap):
        """Cheap change-detection signature straight from a QPixmap.

        Downscales to the coarse grayscale grid without building a full-size
        PIL image, so the high-frequency poll tick stays light. Returns None
        on failure (the caller treats that as 'no change').
        """
        try:
            from PyQt6.QtCore import Qt
            from PyQt6.QtGui import QImage
            img = pixmap.toImage().convertToFormat(QImage.Format.Format_Grayscale8)
            small = img.scaled(config.SCREEN_POLL_COARSE[0],
                               config.SCREEN_POLL_COARSE[1],
                               Qt.AspectRatioMode.IgnoreAspectRatio,
                               Qt.TransformationMode.SmoothTransformation)
            bp = small.bytesPerLine()
            ptr = small.constBits()
            ptr.setsize(bp * small.height())
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape(small.height(), bp)
            arr = arr[:, : small.width()]
            return np.ascontiguousarray(arr).reshape(-1)
        except Exception:
            return None

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
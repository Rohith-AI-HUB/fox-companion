"""Proactive screen commentary.

When the screen reader (``foxio.screen_reader``) detects new on-screen
activity it summarises the screen with a vision model; this module turns
that summary into the fox's spoken line: a speech bubble anchored to the
fox plus a synchronized TTS utterance of the exact same text.

The pipeline is fully autonomous — it never waits for (or depends on) an
explicit user question.  The heavy work (screen grab, change detection,
vision summarization, memory write) lives in the screen reader; here we
only render + speak, throttled so the fox doesn't narrate every flicker.
"""

import time

from core.logger import get_logger

log = get_logger("screen_commentary")

# The single source of truth for the phrase; bubble text and TTS text are
# always identical because both are derived from this one string.
PHRASE_TEMPLATE = "I see you're {summary}."

_COOLDOWN_S = 25.0          # min seconds between proactive commentary lines
_SUPPRESS_AFTER_S = 20.0    # hush other behavior chatter after speaking
_FALLBACK_PREFIX = "using the computer"  # vision couldn't tell — stay quiet


def phrase_for(summary: str) -> str:
    """Build the exact line shown in the bubble and spoken by TTS."""
    return PHRASE_TEMPLATE.format(summary=summary.strip().rstrip("."))


class ScreenCommentary:
    """Render + speak 'I see you're {summary}.' on a new screen observation.

    UI pieces are injected (bubble, voice, window, behavior) so the class
    is unit-testable without a live Qt app; ``mouth_pos`` and
    ``estimate_ms`` adapt it to the host UI's anchoring and bubble sizing.
    """

    def __init__(self, bubble, voice, win, behavior, mouth_pos=None, estimate_ms=None):
        self.bubble = bubble
        self.voice = voice
        self.win = win
        self.behavior = behavior
        self.mouth_pos = mouth_pos
        self.estimate_ms = estimate_ms
        self.cooldown_s = _COOLDOWN_S
        self._last_spoken_at = 0.0
        self.last_spoken_line = None
        # ms from bubble render to TTS worker start (set by on_start callback)
        self.last_tts_sync_ms = None

    def speak_observation(self, summary: str) -> bool:
        """Show the bubble and start TTS for ``summary``.

        Must be called on the GUI thread.  Returns True when a line was
        actually rendered + spoken this call (False when throttled, muted,
        suppressed, hidden, or the summary is the vague vision fallback).
        """
        if self._is_fallback(summary):
            return False
        line = phrase_for(summary)
        now = time.time()
        if now - self._last_spoken_at < self.cooldown_s:
            return False
        if self.voice is None or getattr(self.voice, "muted", False):
            return False
        if self.win is None or not self.win.isVisible():
            return False
        if self.behavior is not None and now < getattr(
                self.behavior, "_suppress_speech_until", 0.0):
            return False  # don't talk over a reply or an active chat
        self._last_spoken_at = now
        self.last_spoken_line = line

        # Bubble + TTS are invoked back-to-back so audio starts within a
        # few ms of the bubble render; on_start below measures the delta.
        pos = self.win.pos()
        mx, my = self.mouth_pos(pos.x(), pos.y()) if self.mouth_pos else (pos.x(), pos.y())
        duration_ms = self.estimate_ms(line) if self.estimate_ms else 3000
        t_show = time.monotonic()
        try:
            self.bubble.show_text(line, mx, my, duration_ms=duration_ms)
        except Exception as e:
            log.error("screen commentary bubble failed: %s", e)

        def _on_tts_start(_text):
            self.last_tts_sync_ms = (time.monotonic() - t_show) * 1000.0
            log.info("screen commentary tts started %.0f ms after bubble",
                     self.last_tts_sync_ms)

        try:
            self.voice.speak(line, on_start=_on_tts_start)
        except Exception as e:
            log.error("screen commentary tts failed: %s", e)

        if self.behavior is not None:
            self.behavior.suppress_speech_for(_SUPPRESS_AFTER_S)
        return True

    @staticmethod
    def _is_fallback(summary: str) -> bool:
        """True when the vision model fell back to its generic line."""
        return summary.strip().lower().startswith(_FALLBACK_PREFIX)

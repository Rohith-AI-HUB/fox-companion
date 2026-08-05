"""Tests for the proactive screen-comment feature (bubble + TTS).

Covers the autonomous pipeline: the screen reader's observation → the
phrase ``"I see you're {summary}."`` → bubble + TTS, plus throttling and
the various guards (mute, hidden window, speech suppression, vision
fallback).  No user question is ever required for a response.
"""

from PyQt6.QtCore import QPoint

from core import config
from foxio.screen_commentary import ScreenCommentary, phrase_for
from foxio.screen_reader import ScreenReader

SUMMARY = "editing main.py (Python) in the fox-companion project in VS Code"
EXPECTED_LINE = f"I see you're {SUMMARY}."


class FakeVoice:
    def __init__(self):
        self.muted = False
        self.speaks = []
        self.on_start = None

    def speak(self, text, on_start=None, on_end=None):
        self.speaks.append(text)
        self.on_start = on_start


class FakeBubble:
    def __init__(self):
        self.shown = []

    def show_text(self, text, x, y, duration_ms=None):
        self.shown.append((text, x, y, duration_ms))


class FakeWin:
    def __init__(self, visible=True):
        self._visible = visible

    def pos(self):
        return QPoint(100, 200)

    def isVisible(self):
        return self._visible


class FakeBehavior:
    def __init__(self):
        self._suppress_speech_until = 0.0
        self.suppressed_for = []

    def suppress_speech_for(self, seconds):
        self.suppressed_for.append(seconds)


def _make(**kwargs):
    voice = kwargs.get("voice", FakeVoice())
    bubble = kwargs.get("bubble", FakeBubble())
    win = kwargs.get("win", FakeWin())
    behavior = kwargs.get("behavior", FakeBehavior())
    commentary = ScreenCommentary(
        bubble, voice, win, behavior,
        mouth_pos=kwargs.get("mouth_pos", lambda x, y: (x + 5, y + 10)),
        estimate_ms=kwargs.get("estimate_ms", lambda text: 3000),
    )
    if "cooldown_s" in kwargs:
        commentary.cooldown_s = kwargs["cooldown_s"]
    return commentary, voice, bubble, win, behavior


# ── phrase format ───────────────────────────────────────────────────

def test_phrase_format_is_exact_template():
    assert phrase_for(SUMMARY) == EXPECTED_LINE
    assert phrase_for("  browsing the web  ") == "I see you're browsing the web."


# ── render + speak (visual and audio execution) ────────────────────

def test_speak_observation_shows_bubble_and_speaks_same_text():
    commentary, voice, bubble, _, behavior = _make()
    ok = commentary.speak_observation(SUMMARY)

    assert ok is True
    assert voice.speaks == [EXPECTED_LINE]                       # TTS text
    assert bubble.shown == [(EXPECTED_LINE, 105, 210, 3000)]     # bubble text + anchored pos
    assert behavior.suppressed_for == [20.0]


def test_bubble_anchored_to_fox_mouth_and_sized_by_estimate():
    mouth_pos = lambda x, y: (x + 40, y + 43)
    estimate_ms = lambda text: 2750
    commentary, voice, bubble, win, behavior = _make(
        mouth_pos=mouth_pos, estimate_ms=estimate_ms)

    commentary.speak_observation(SUMMARY)
    assert bubble.shown[0][1:] == (140, 243, 2750)


# ── guards / excluded patterns ─────────────────────────────────────

def test_throttled_by_cooldown():
    commentary, voice, bubble, win, behavior = _make(cooldown_s=25.0)
    assert commentary.speak_observation(SUMMARY) is True
    assert commentary.speak_observation("writing a document") is False
    assert len(voice.speaks) == 1


def test_muted_voice_stays_silent():
    voice = FakeVoice()
    voice.muted = True
    commentary, _, bubble, win, behavior = _make(voice=voice)
    assert commentary.speak_observation(SUMMARY) is False
    assert voice.speaks == []
    assert bubble.shown == []


def test_hidden_window_stays_silent():
    commentary, voice, bubble, win, behavior = _make(win=FakeWin(visible=False))
    assert commentary.speak_observation(SUMMARY) is False
    assert voice.speaks == []


def test_speech_suppression_respected():
    behavior = FakeBehavior()
    behavior._suppress_speech_until = 10 ** 12  # far in the future
    commentary, voice, bubble, win, _ = _make(behavior=behavior)
    assert commentary.speak_observation(SUMMARY) is False
    assert voice.speaks == []


def test_vague_vision_fallback_is_not_narrated():
    commentary, voice, bubble, win, behavior = _make()
    assert commentary.speak_observation("using the computer") is False
    assert voice.speaks == []
    assert bubble.shown == []


def test_tts_start_is_measured_against_bubble_render():
    commentary, voice, bubble, win, behavior = _make()
    assert commentary.speak_observation(SUMMARY) is True
    assert voice.on_start is not None
    voice.on_start("ignored")
    assert commentary.last_tts_sync_ms is not None
    assert commentary.last_tts_sync_ms >= 0.0


# ── autonomous (no user question) pipeline ─────────────────────────

def test_screen_reader_observe_is_autonomous_and_store_in_memory():
    sr = ScreenReader.__new__(ScreenReader)
    sr._active = True
    sr._groq = object()
    sr._openai = None
    sr._last_observe = None
    sr._cooldown_until = 0.0
    sr._png_bytes = lambda img: b"fake-png"

    observed = []
    facts = []

    class FakeBrain:
        def capture_async(self, fact, user_id, on_done=None):
            facts.append(fact)

    sr.brain = FakeBrain()
    sr.on_observe = observed.append
    sr._ask_groq = lambda png: SUMMARY

    sr._observe(object())

    # Driven purely by a screen change (no user question involved).
    assert observed == [SUMMARY]
    assert facts == [f"{config.SCREEN_SOURCE_LABEL}: {SUMMARY}"]
    assert sr.latest_observation == SUMMARY


def test_screen_reader_skips_duplicate_memory_write():
    sr = ScreenReader.__new__(ScreenReader)
    sr._active = True
    sr._groq = object()
    sr._openai = None
    sr._last_observe = SUMMARY  # already observed
    sr._cooldown_until = 0.0
    sr._png_bytes = lambda img: b"fake-png"
    sr._ask_groq = lambda png: SUMMARY

    observed = []
    facts = []

    class FakeBrain:
        def capture_async(self, fact, user_id, on_done=None):
            facts.append(fact)

    sr.brain = FakeBrain()
    sr.on_observe = observed.append

    sr._observe(object())

    assert observed == []
    assert facts == []


def test_latest_observation_empty_before_any_observe():
    sr = ScreenReader.__new__(ScreenReader)
    sr._last_observe = None
    assert sr.latest_observation == ""
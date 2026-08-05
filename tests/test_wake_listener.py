"""Tests for :class:`foxio.wake_listener.WakeListener` scoring logic.

The streaming scorer always supplies exactly ``N_FEATURE_FRAMES`` frames per
prediction. The verifier is loaded lazily; openwakeword/sounddevice are real
libraries and are never needed by these tests (they run on a fake
preprocessor + fake verifier).
"""
import os
import queue
from collections import deque

import numpy as np
import pytest

from core import config
from foxio.wake_listener import WakeListener, N_FEATURE_FRAMES, CHUNK_SIZE


class FakeFeatures:
    """Stands in for openwakeword.AudioFeatures: callable + get_features()."""

    def __init__(self, n_frames):
        self._n_frames = n_frames

    def __call__(self, _chunk):
        return None

    def get_features(self, _count):
        return np.zeros((768, self._n_frames), dtype=np.float32)


class FakeVerifier:
    def predict_proba(self, _x):
        return np.array([[0.2, 0.8]])  # P(positive) = 0.8


@pytest.fixture
def listener():
    wake = WakeListener.__new__(WakeListener)  # skip VAD/queue setup
    wake.preprocessor = FakeFeatures(N_FEATURE_FRAMES)
    wake.verifier = FakeVerifier()
    return wake


def test_wake_constant_available():
    assert N_FEATURE_FRAMES == 10


def test_score_returns_zero_when_window_not_full(listener):
    # Only 3 feature frames buffered -> below the 10-frame gate.
    listener.preprocessor = FakeFeatures(3)
    assert listener._score_with_verifier(np.zeros(1280, dtype=np.float32)) == 0.0


def test_score_uses_verifier_when_window_full(listener):
    score = listener._score_with_verifier(np.zeros(1280, dtype=np.float32))
    assert score == pytest.approx(0.8)


def test_score_bounds(listener):
    listener.preprocessor = FakeFeatures(N_FEATURE_FRAMES)
    for _ in range(5):
        score = listener._score_with_verifier(np.random.randn(1280).astype(np.float32))
        assert 0.0 <= score <= 1.0


def test_process_audio_skips_when_no_verifier():
    # Real construction is safe (no mic / no openwakeword imported eagerly).
    wake = WakeListener()
    wake.verifier = None
    # _process_audio must no-op (guard: verifier is None) — no crash.
    assert wake._process_audio() is None


# ── Keyword confirmation gate ───────────────────────────────────────

def _gated_listener(on_wake=None):
    """A scoring-capable listener with a controllable confirmation gate."""
    wake = WakeListener.__new__(WakeListener)
    wake.preprocessor = FakeFeatures(N_FEATURE_FRAMES)
    wake.verifier = FakeVerifier()
    wake.wake_threshold = 0.45
    wake._last_trigger_at = 0.0
    wake.audio_queue = queue.Queue()
    wake.frame_buffer = deque(maxlen=50)
    wake.audio_buffer = deque(maxlen=config.WAKE_CONFIRM_WINDOW_CHUNKS)
    wake.on_wake = on_wake
    wake._init_learning()
    return wake


# -- transcript keyword check ----------------------------------------

def test_confirm_transcript_confirms_hey_fox():
    assert WakeListener._confirm_transcript("Hey Fox")[0] is True
    assert WakeListener._confirm_transcript("hey fox")[0] is True


def test_confirm_transcript_confirms_fox_alone():
    assert WakeListener._confirm_transcript("fox")[0] is True


def test_confirm_transcript_ignores_greetings():
    for greeting in ("hey", "hi", "hello", "hii", "hai", "yo", "hey there"):
        ok, reason = WakeListener._confirm_transcript(greeting)
        assert ok is False, f"{greeting!r} should be rejected, got {reason!r}"


def test_confirm_transcript_ignores_empty():
    assert WakeListener._confirm_transcript("") == (False, "no speech text")
    assert WakeListener._confirm_transcript("   ") == (False, "no speech text")


def test_confirm_transcript_is_case_insensitive():
    assert WakeListener._confirm_transcript("HEY FOX")[0] is True


# -- trigger decision (transcription + fallback score) ---------------

def test_confirm_trigger_uses_transcript(monkeypatch):
    lst = _gated_listener()
    monkeypatch.setattr(lst, "_confirm_utterance", lambda: "hey fox")
    assert lst._confirm_trigger(0.5) == (True, "contains wake keyword")


def test_confirm_trigger_rejects_greeting_even_at_high_score(monkeypatch):
    lst = _gated_listener()
    monkeypatch.setattr(lst, "_confirm_utterance", lambda: "hey")
    assert lst._confirm_trigger(0.9)[0] is False


def test_confirm_trigger_fallback_needs_high_score_when_no_asr(monkeypatch):
    lst = _gated_listener()
    monkeypatch.setattr(lst, "_confirm_utterance", lambda: "")
    assert lst._confirm_trigger(config.WAKE_CONFIRM_FALLBACK_SCORE)[0] is True
    assert lst._confirm_trigger(0.40)[0] is False


# -- end-to-end gating in the audio loop -----------------------------

def test_process_audio_emits_only_when_confirmed(monkeypatch):
    fired = []
    listener = _gated_listener(on_wake=lambda: fired.append(1))
    # Model scores this chunk 0.8 (> 0.45) — a candidate.
    monkeypatch.setattr(listener, "_confirm_trigger", lambda score: (True, "test"))
    listener.audio_queue.put(np.zeros(CHUNK_SIZE, dtype=np.int16))
    listener._process_audio()
    assert fired == [1]


def test_process_audio_suppresses_greeting(monkeypatch):
    fired = []
    listener = _gated_listener(on_wake=lambda: fired.append(1))
    # Same high score, but confirmation rejects the greeting.
    monkeypatch.setattr(listener, "_confirm_trigger", lambda score: (False, "greeting"))
    listener.audio_queue.put(np.zeros(CHUNK_SIZE, dtype=np.int16))
    listener._process_audio()
    assert fired == []


# -- buffered audio -----------------------------------------------

def test_buffer_to_wav_empty_is_none():
    assert _gated_listener()._buffer_to_wav() is None


def test_buffer_to_wav_builds_riif_bytes():
    wake = _gated_listener()
    wake.audio_buffer.append(np.zeros(CHUNK_SIZE, dtype=np.int16).tobytes())
    wav = wake._buffer_to_wav()
    assert wav is not None
    assert wav[:4] == b"RIFF"
    assert b"WAVE" in wav[:12]


# -- self-learning (adaptive wake word) -----------------------------

def _learning_listener(tmp_path):
    lst = _gated_listener()
    lst.learn_enabled = True
    lst.learn_dir = str(tmp_path)
    return lst


def _fill_buffer(lst, value=500, n=5):
    chunk = np.full(CHUNK_SIZE, value, dtype=np.int16)
    for _ in range(n):
        lst.audio_buffer.append(chunk.tobytes())


def _saved(lst, label):
    prefix = f"{label}_"
    return [f for f in os.listdir(lst.learn_dir)
            if f.startswith(prefix) and f.endswith(".npy")]


def test_learn_saves_positive_on_confirmed_keyword(tmp_path):
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst)
    lst._maybe_learn(True, "contains wake keyword")
    assert len(_saved(lst, "pos")) == 1


def test_learn_skips_positive_on_fallback_confirmation(tmp_path):
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst)
    lst._maybe_learn(True, "high-confidence fallback (score 0.700)")
    assert _saved(lst, "pos") == []


def test_learn_saves_negative_on_greeting_rejection(tmp_path):
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst)
    lst._maybe_learn(False, "greeting without wake keyword")
    assert len(_saved(lst, "neg")) == 1


def test_learn_saves_negative_on_no_wake_keyword(tmp_path):
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst)
    lst._maybe_learn(False, "no wake keyword")
    assert len(_saved(lst, "neg")) == 1


def test_learn_skips_ambiguous_rejections(tmp_path):
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst)
    lst._maybe_learn(False, "no speech text")
    lst._maybe_learn(False, "no transcription and score 0.500 below fallback")
    assert _saved(lst, "neg") == []


def test_learn_skips_when_disabled(tmp_path):
    lst = _learning_listener(tmp_path)
    lst.learn_enabled = False
    _fill_buffer(lst)
    lst._maybe_learn(True, "contains wake keyword")
    assert _saved(lst, "pos") == []


def test_learn_respects_throttle(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WAKE_LEARN_THROTTLE_S", 100.0)
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst)
    lst._maybe_learn(True, "contains wake keyword")
    lst._maybe_learn(True, "contains wake keyword")
    assert len(_saved(lst, "pos")) == 1


def test_learn_skips_silent_buffer(tmp_path):
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst, value=0)  # zeros -> rms 0 < floor
    lst._maybe_learn(True, "contains wake keyword")
    assert _saved(lst, "pos") == []


def test_learn_caps_clips(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WAKE_LEARN_MAX_CLIPS", 1)
    lst = _learning_listener(tmp_path)
    _fill_buffer(lst)
    np.save(os.path.join(lst.learn_dir, "pos_x.npy"), np.zeros(1280, dtype=np.int16))
    lst._maybe_learn(True, "contains wake keyword")
    assert _saved(lst, "pos") == ["pos_x.npy"]


def test_learn_triggers_retrain_after_threshold(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "WAKE_LEARN_RETRAIN_AFTER", 1)
    calls = []
    lst = _learning_listener(tmp_path)
    lst._schedule_retrain = lambda: calls.append(1)
    _fill_buffer(lst)
    lst._maybe_learn(True, "contains wake keyword")
    assert calls == [1]


def test_buffer_to_int16_roundtrip():
    lst = _gated_listener()
    chunk = np.arange(CHUNK_SIZE, dtype=np.int16)
    lst.audio_buffer.append(chunk.tobytes())
    out = lst._buffer_to_int16()
    assert out.dtype == np.int16
    assert len(out) == CHUNK_SIZE
    np.testing.assert_array_equal(out, chunk)
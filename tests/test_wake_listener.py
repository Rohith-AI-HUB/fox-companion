"""Tests for :class:`foxio.wake_listener.WakeListener` scoring logic.

The streaming scorer always supplies exactly ``N_FEATURE_FRAMES`` frames per
prediction. The verifier is loaded lazily; openwakeword/sounddevice are real
libraries and are never needed by these tests (they run on a fake
preprocessor + fake verifier).
"""
import numpy as np
import pytest

from foxio.wake_listener import WakeListener, N_FEATURE_FRAMES


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
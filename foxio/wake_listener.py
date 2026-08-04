import os
import pickle
import time
import numpy as np
import threading
import queue
from collections import deque

from foxio.vad import VoiceActivityDetector, SAMPLE_RATE, FRAME_SIZE
from core import config
from core.logger import get_logger

log = get_logger("wake")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VERIFIER_PATH = os.path.join(BASE_DIR, "assets", "wake", "fox_verifier.pkl")

WAKE_THRESHOLD = config.WAKE_THRESHOLD_DEFAULT
WAKE_COOLDOWN_S = 3.0
STREAM_RETRY_S = 2.0

CHUNK_SIZE = 1280
INPUT_SAMPLE_RATE = 16000
# Number of 1280-sample feature frames scored per decision. The wake word is
# always the LAST word spoken, so we only need a short window (~0.8s) that
# covers the final syllable. A shorter window removes the preceding-speech
# context that otherwise makes control phrases look like the wake word.
N_FEATURE_FRAMES = 10


def flatten_features(x):
    """Feature-flattening helper referenced by the pickled verifier pipeline."""
    return [i.flatten() for i in x]


class WakeListener:
    """Streaming wake-word listener for 'Hey Fox' / 'Fox'.

    Scoring is always-on (one 1280-sample frame per call, the openWakeWord
    streaming pattern) and gated only by the model score, so quiet or distant
    utterances are still scored. Detection is done by the custom verifier
    model (``fox_verifier.pkl``).
    """

    def __init__(self, on_wake=None, threshold=None):
        self.on_wake = on_wake
        self.running = False
        self._thread = None

        self.vad = VoiceActivityDetector(mode=1)
        self.verifier = None
        self.preprocessor = None

        # Trigger threshold: explicit arg > settings.json > default
        self.wake_threshold = threshold
        if self.wake_threshold is None:
            self.wake_threshold = float(
                config.load_settings().get("wake_threshold", config.WAKE_THRESHOLD_DEFAULT)
            )

        self._last_trigger_at = 0.0

        self.audio_queue = queue.Queue()
        self.frame_buffer = deque(maxlen=50)

    def start(self):
        if self.running:
            return

        if not self._load_detector():
            return

        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("wake listener started")

    def _load_detector(self):
        """Load the wake detector (``fox_verifier.pkl``).

        Separated from ``start()`` so tests can load the detector without
        opening the microphone. Returns True if a detector became available.
        """
        if os.path.exists(VERIFIER_PATH):
            try:
                with open(VERIFIER_PATH, 'rb') as f:
                    self.verifier = pickle.load(f)
                from openwakeword.utils import AudioFeatures
                self.preprocessor = AudioFeatures(inference_framework='onnx')
                log.info("loaded fox_verifier.pkl (threshold=%.3f)", self.wake_threshold)
            except Exception as e:
                log.warning("failed to load verifier: %s", e)
                self.verifier = None

        if self.verifier is None:
            log.warning("no wake detector available — wake word disabled")
            return False
        return True

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        """Audio capture loop. Re-opens the stream after transient errors so a
        single audio glitch no longer kills the wake listener permanently."""
        while self.running:
            try:
                import sounddevice as sd
                with sd.InputStream(
                    samplerate=INPUT_SAMPLE_RATE,
                    channels=1,
                    dtype=np.int16,
                    blocksize=CHUNK_SIZE,
                    callback=self._audio_callback
                ):
                    while self.running:
                        self._process_audio()
            except Exception as e:
                log.error("wake listener stream error: %s", e)
                if not self.running:
                    break
                # Drop stale audio so scoring restarts cleanly
                while not self.audio_queue.empty():
                    try:
                        self.audio_queue.get_nowait()
                    except queue.Empty:
                        break
                time.sleep(STREAM_RETRY_S)

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.debug("audio status: %s", status)
        self.audio_queue.put(indata.copy())

    # ── Scoring ──────────────────────────────────────────────────────

    def _score_with_verifier(self, chunk):
        self.preprocessor(chunk)
        feats = self.preprocessor.get_features(N_FEATURE_FRAMES)
        if feats.shape[1] < N_FEATURE_FRAMES:
            return 0.0
        return float(self.verifier.predict_proba(feats.reshape(1, -1))[0, 1])

    # ── Processing ───────────────────────────────────────────────────

    def _process_audio(self):
        try:
            chunk = self.audio_queue.get(timeout=0.1)
        except queue.Empty:
            return

        chunk_float = chunk.ravel().astype(np.float32)

        # Always-on streaming scoring: one 1280-sample frame per predict call
        # (the openWakeWord reference pattern). The model score is the detector;
        # no VAD gating, so quiet/distant wake words are still scored.
        if self.verifier is None:
            return
        score = self._score_with_verifier(chunk_float)

        log.debug("wake score=%.4f", score)

        if score > self.wake_threshold:
            now = time.monotonic()
            if now - self._last_trigger_at < WAKE_COOLDOWN_S:
                return
            self._last_trigger_at = now
            log.info("wake word detected! score=%.3f", score)
            if self.on_wake:
                self.on_wake()
            # Reset model/preprocessor state so the next wake starts clean
            self._reset_detector()

    def _reset_detector(self):
        if self.preprocessor is not None:
            try:
                self.preprocessor.reset()
            except Exception:
                pass
        try:
            self.frame_buffer.clear()
        except Exception:
            pass

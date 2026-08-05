import io
import os
import pickle
import re
import subprocess
import sys
import time
import wave
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

# Rejection reasons that prove the buffered utterance was real, non-fox speech
# (the transcript gate said so, not the model). Only these are safe to save as
# self-learned negatives; ambiguous rejections ("no speech text", ASR down with
# a below-fallback score) are skipped so a genuine quiet "fox" is never
# mislabeled as a negative.
LEARN_NEGATIVE_REASONS = frozenset({"greeting without wake keyword", "no wake keyword"})
# Only ASR-confirmed wakes (transcript contains "fox") are saved as positives.
# The high-confidence score fallback is NOT used for training, because it cannot
# distinguish a quiet "fox" from a loud non-fox phrase and would poison the data.
LEARN_POSITIVE_REASON = "contains wake keyword"

# Background retrain command (project-relative path, run from BASE_DIR).
RETRAIN_CMD = [sys.executable, os.path.join("tools", "train_verifier.py")]

# 20 ms frame length for silence trimming of runtime-saved clips.
TRIM_FRAME = int(INPUT_SAMPLE_RATE * 0.020)


def _trim_edges(clip: np.ndarray, thr: float = 0.02, pad_s: float = 0.05) -> np.ndarray:
    """Trim leading/trailing near-silence from a runtime-saved clip.

    Mirrors tools/enroll_record.trim() so self-learned clips look like the
    explicitly enrolled ones (trimmed, wake word at the end).
    """
    n = len(clip) // TRIM_FRAME
    if n == 0:
        return clip
    rms = np.array([
        float(np.sqrt(np.mean(clip[i * TRIM_FRAME:(i + 1) * TRIM_FRAME].astype(np.float64) ** 2)))
        for i in range(n)
    ])
    peak = max(rms.max(), 1.0)
    on = rms > peak * thr
    if not on.any():
        return clip
    idx = np.where(on)[0]
    s = max(0, idx[0] - int(pad_s / 0.020))
    e = min(n, idx[-1] + int(pad_s / 0.020) + 1)
    return clip[s * TRIM_FRAME:e * TRIM_FRAME]


def flatten_features(x):
    """Feature-flattening helper referenced by the pickled verifier pipeline."""
    return [i.flatten() for i in x]


class WakeListener:
    """Streaming wake-word listener for 'Hey Fox' / 'Fox'.

    Scoring is always-on (one 1280-sample frame per call, the openWakeWord
    streaming pattern) and gated by a keyword-confirmation pass: a score
    above the threshold marks a candidate, which is confirmed by
    transcribing the buffered audio and requiring the word "fox". This
    rejects casual greetings ("hey", "hi", ...) that the model otherwise
    scores highly. Detection itself is done by the custom verifier model
    (``fox_verifier.pkl``).
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
        # Raw int16 audio kept for the keyword-confirmation gate (~2 s window).
        self.audio_buffer = deque(maxlen=config.WAKE_CONFIRM_WINDOW_CHUNKS)

        self._init_learning()

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

        # Keep the raw int16 audio in a sliding buffer for the keyword gate.
        self.audio_buffer.append(chunk.tobytes())

        chunk_float = chunk.ravel().astype(np.float32)

        # Always-on streaming scoring: one 1280-sample frame per predict call
        # (the openWakeWord reference pattern). The score is the *primary*
        # detector; no VAD gating, so quiet/distant wake words are still scored.
        if self.verifier is None:
            return
        score = self._score_with_verifier(chunk_float)

        log.debug("wake score=%.4f", score)

        if score > self.wake_threshold:
            now = time.monotonic()
            if now - self._last_trigger_at < WAKE_COOLDOWN_S:
                return
            self._last_trigger_at = now
            # A high score is only a *candidate*. Confirm it by transcribing
            # the buffered utterance and requiring the wake keyword, so casual
            # greetings ("hey", "hi", ...) never trigger a response.
            confirmed, reason = self._confirm_trigger(score)
            if not confirmed:
                log.info("wake candidate ignored (score=%.3f, %s)", score, reason)
                self._maybe_learn(False, reason)
                return
            log.info("wake word detected! score=%.3f (%s)", score, reason)
            # Self-learn BEFORE waking so the buffer still holds the exact
            # wake utterance (later chunks are appended only in this loop).
            self._maybe_learn(True, reason)
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

    # ── Self-learning (adaptive wake word) ────────────────────────────

    def _init_learning(self):
        """Initialize the passive self-learning state.

        Separated from ``__init__`` so tests can build a listener with
        ``__new__`` and still enable learning.
        """
        self.learn_enabled = bool(
            config.load_settings().get("wake_learn_enabled", config.WAKE_LEARN_ENABLED)
        )
        self.learn_dir = config.WAKE_LEARN_DIR
        self._learn_last_saved = {"pos": 0.0, "neg": 0.0}
        self._learn_new_clips = 0
        self._retrain_lock = threading.Lock()
        self._retrain_running = False

    def _maybe_learn(self, confirmed, reason):
        """Label the buffered utterance from the confirmation-gate verdict.

        Only decisions the *transcript* made are trustworthy enough for
        training: a confirmed wake (transcript contains "fox") is a positive;
        a rejected candidate whose transcript was real non-fox speech is a
        negative. Model-score fallback decisions are never learned.
        """
        if not self.learn_enabled:
            return
        if confirmed:
            if reason == LEARN_POSITIVE_REASON:
                self._learn_save("pos")
        else:
            if reason in LEARN_NEGATIVE_REASONS:
                self._learn_save("neg")

    def _learn_save(self, label):
        """Save the buffered utterance as a training clip (throttled, capped).

        Returns True if a clip was saved. Runs on the wake-listener audio
        thread; the background retrain runs in its own daemon thread.
        """
        if not self.audio_buffer:
            return False
        if time.monotonic() - self._learn_last_saved.get(label, 0.0) < config.WAKE_LEARN_THROTTLE_S:
            return False
        clip = self._buffer_to_int16()
        if len(clip) < CHUNK_SIZE:
            return False
        rms = float(np.sqrt(np.mean(clip.astype(np.float64) ** 2)))
        if rms < config.WAKE_LEARN_MIN_RMS:
            log.debug("wake self-learn: skipped %s (rms %.0f below floor)", label, rms)
            return False
        if self._learn_count(label) >= config.WAKE_LEARN_MAX_CLIPS:
            return False
        clip = _trim_edges(clip)
        if len(clip) < CHUNK_SIZE:
            return False
        try:
            os.makedirs(self.learn_dir, exist_ok=True)
            path = os.path.join(self.learn_dir, f"{label}_{int(time.time())}.npy")
            np.save(path, clip)
        except OSError as e:
            log.warning("wake self-learn: save failed: %s", e)
            return False
        self._learn_last_saved[label] = time.monotonic()
        self._learn_new_clips += 1
        log.info("wake self-learn: saved %s clip -> %s", label, os.path.basename(path))
        if self._learn_new_clips >= config.WAKE_LEARN_RETRAIN_AFTER:
            self._schedule_retrain()
        return True

    def _learn_count(self, label):
        """Number of saved clips of ``label`` (pos_/neg_ prefix, .npy)."""
        if not os.path.isdir(self.learn_dir):
            return 0
        prefix = f"{label}_"
        return sum(1 for f in os.listdir(self.learn_dir)
                   if f.startswith(prefix) and f.endswith(".npy"))

    def _buffer_to_int16(self):
        """Assemble the buffered int16 chunk bytes into a single int16 array."""
        if not self.audio_buffer:
            return np.empty(0, dtype=np.int16)
        return np.frombuffer(b"".join(self.audio_buffer), dtype=np.int16).copy()

    def _schedule_retrain(self):
        """Kick off a background retrain unless one is already running."""
        with self._retrain_lock:
            if self._retrain_running:
                return
            self._retrain_running = True
        threading.Thread(target=self._retrain_worker, daemon=True).start()

    def _retrain_worker(self):
        """Run tools/train_verifier.py in the background, then hot-reload."""
        try:
            log.info("wake self-learn: starting background retrain (%s)",
                     " ".join(RETRAIN_CMD))
            os.makedirs(os.path.dirname(config.WAKE_LEARN_RETRAIN_LOG), exist_ok=True)
            with open(config.WAKE_LEARN_RETRAIN_LOG, "a", encoding="utf-8") as logf:
                proc = subprocess.Popen(
                    RETRAIN_CMD, cwd=BASE_DIR, stdout=logf, stderr=subprocess.STDOUT
                )
            ret = proc.wait()
            if ret == 0:
                self._reload_verifier()
                log.info("wake self-learn: retrain finished, verifier hot-reloaded")
            else:
                log.warning("wake self-learn: retrain exited code %s", ret)
        except Exception as e:
            log.warning("wake self-learn: retrain failed: %s", e)
        finally:
            with self._retrain_lock:
                self._retrain_running = False
            self._learn_new_clips = 0

    def _reload_verifier(self):
        """Swap in the freshly trained verifier without restarting the app."""
        try:
            with open(VERIFIER_PATH, "rb") as f:
                new_verifier = pickle.load(f)
        except Exception as e:
            log.warning("wake self-learn: reload failed: %s", e)
            return
        self.verifier = new_verifier
        self._reset_detector()
        log.info("wake verifier hot-reloaded from %s", VERIFIER_PATH)

    # ── Keyword confirmation ────────────────────────────────────────

    def _confirm_trigger(self, score):
        """Decide whether a measured wake candidate is a real wake.

        Two-layer gate:
        1. Transcribe the buffered audio; confirm only if the transcript
           contains the wake keyword and reject pure greetings.
        2. If transcription is unavailable (no network/service), fall back to a
           high-confidence score so genuine wake words still work.
        Returns ``(confirmed, reason)``.
        """
        transcript = self._confirm_utterance()
        if transcript:
            return self._confirm_transcript(transcript)
        if score >= config.WAKE_CONFIRM_FALLBACK_SCORE:
            return True, f"high-confidence fallback (score {score:.3f})"
        return False, f"no transcription and score {score:.3f} below fallback"

    def _confirm_utterance(self):
        """Transcribe the buffered utterance via SpeechRecognition (Google)."""
        try:
            import speech_recognition as sr
        except Exception as e:  # pragma: no cover - library present at runtime
            log.warning("wake confirm unavailable: %s", e)
            return ""
        wav = self._buffer_to_wav()
        if not wav:
            return ""
        try:
            rec = sr.Recognizer()
            with sr.AudioFile(io.BytesIO(wav)) as source:
                audio = rec.record(source)
            return rec.recognize_google(audio)
        except Exception as e:
            log.warning("wake confirm transcription failed: %s", e)
            return ""

    @staticmethod
    def _confirm_transcript(transcript):
        """Keyword check against the transcribed utterance.

        Returns ``(confirmed, reason)``. A wake is confirmed only when the word
        "fox" is present; pure greetings ("hey", "hi", "hello", ...) that do not
        address the fox are explicitly rejected.
        """
        if not transcript or not transcript.strip():
            return False, "no speech text"
        t = transcript.strip().lower()
        if config.WAKE_CONFIRM_KEYWORD in t:
            return True, "contains wake keyword"
        words = re.findall(r"[a-z']+", t)
        if words and all(w in config.WAKE_GREETING_STOP_WORDS for w in words):
            return False, "greeting without wake keyword"
        return False, "no wake keyword"

    def _buffer_to_wav(self):
        """Assemble the buffered int16 chunks into a mono 16 kHz WAV."""
        if not self.audio_buffer:
            return None
        frames = b"".join(self.audio_buffer)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(INPUT_SAMPLE_RATE)
            wf.writeframes(frames)
        return buf.getvalue()

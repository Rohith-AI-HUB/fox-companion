import os
import pickle
import numpy as np
import sounddevice as sd
import threading
import queue
from collections import deque

from openwakeword import Model
from openwakeword.utils import AudioFeatures
from foxio.vad import VoiceActivityDetector, SAMPLE_RATE, FRAME_SIZE
from core.logger import get_logger

log = get_logger("wake")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOX_MODEL_PATH = os.path.join(BASE_DIR, "assets", "wake", "fox.onnx")
VERIFIER_PATH = os.path.join(BASE_DIR, "assets", "wake", "fox_verifier.pkl")

WAKE_THRESHOLD = 0.5
POST_SPEECH_CHUNKS = 10

CHUNK_SIZE = 1280
INPUT_SAMPLE_RATE = 16000

class WakeListener:
    def __init__(self, on_wake=None):
        self.on_wake = on_wake
        self.running = False
        self._thread = None

        self.vad = VoiceActivityDetector(mode=1)
        self.oww_model = None
        self.verifier = None
        self.preprocessor = None

        self.audio_queue = queue.Queue()
        self.frame_buffer = deque(maxlen=50)

    def start(self):
        if self.running:
            return

        # Try verifier first (trained on current feature pipeline)
        if os.path.exists(VERIFIER_PATH):
            try:
                with open(VERIFIER_PATH, 'rb') as f:
                    self.verifier = pickle.load(f)
                self.preprocessor = AudioFeatures(inference_framework='onnx')
                log.info("loaded fox_verifier.pkl")
            except Exception as e:
                log.warning("failed to load verifier: %s", e)
                self.verifier = None

        # Fall back to ONNX model
        if self.verifier is None:
            if not os.path.exists(FOX_MODEL_PATH):
                log.warning("fox.onnx not found at %s — wake word disabled", FOX_MODEL_PATH)
                return
            try:
                self.oww_model = Model(
                    wakeword_models=[FOX_MODEL_PATH],
                    inference_framework="onnx"
                )
                log.info("loaded fox.onnx")
            except Exception as e:
                log.error("failed to load fox.onnx: %s", e)
                return

        self.running = True
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        log.info("wake listener started")

    def stop(self):
        self.running = False
        if self._thread:
            self._thread.join(timeout=2)

    def _run(self):
        try:
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
            log.error("wake listener error: %s", e)
            self.running = False

    def _audio_callback(self, indata, frames, time_info, status):
        if status:
            log.debug("audio status: %s", status)
        self.audio_queue.put(indata.copy())

    def _score_with_verifier(self, buffer):
        self.preprocessor(buffer)
        feats = self.preprocessor.get_features(22)
        if feats.shape[1] < 22:
            return 0.0
        return float(self.verifier.predict_proba(feats.reshape(1, -1))[0, 1])

    def _score_with_onnx(self, buffer):
        prediction = self.oww_model.predict(buffer)
        return prediction.get("fox", 0.0)

    def _process_audio(self):
        try:
            chunk = self.audio_queue.get(timeout=0.1)
        except queue.Empty:
            return

        chunk_float = chunk.ravel().astype(np.float32)
        self.frame_buffer.append(chunk_float)

        is_speech = self.vad.is_speech(chunk_float, INPUT_SAMPLE_RATE)

        if is_speech:
            buffer = np.concatenate(list(self.frame_buffer))
            if len(buffer) >= CHUNK_SIZE:
                if self.verifier is not None:
                    score = self._score_with_verifier(buffer)
                else:
                    score = self._score_with_onnx(buffer)

                log.info("wake score=%.4f", score)
                if score > WAKE_THRESHOLD:
                    log.info("wake word detected! score=%.3f", score)
                    if self.on_wake:
                        self.on_wake()
                    self.vad.reset()
                    self.frame_buffer.clear()

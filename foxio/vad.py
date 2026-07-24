import numpy as np
import struct
from collections import deque

SAMPLE_RATE = 16000
FRAME_MS = 30
FRAME_SIZE = int(SAMPLE_RATE * FRAME_MS / 1000)

class VoiceActivityDetector:
    def __init__(self, mode=0, sample_rate=SAMPLE_RATE, frame_ms=FRAME_MS):
        self.sample_rate = sample_rate
        self.frame_size = int(sample_rate * frame_ms / 1000)
        self.aggressiveness = mode

        self.energy_history = deque(maxlen=20)

        self.speech_buffer = deque(maxlen=6)

        self.hangover = 0
        self.hangover_max = {0: 8, 1: 6, 2: 4, 3: 2}.get(mode, 6)
        self.noise_floor = 0.0
        self.noise_alpha = 0.99

    def _rms(self, frame):
        if len(frame) == 0:
            return 0.0
        return np.sqrt(np.mean(frame.astype(np.float64) ** 2))

    def _is_speech_frame(self, frame):
        rms = self._rms(frame)
        self.energy_history.append(rms)

        self.noise_floor = self.noise_alpha * self.noise_floor + (1 - self.noise_alpha) * rms

        threshold = max(self.noise_floor * 2.5, 0.005 * (self.aggressiveness + 1))
        return rms > threshold

    def is_speech(self, frame, sample_rate=None):
        if sample_rate and sample_rate != self.sample_rate:
            frame = self._resample(frame, sample_rate, self.sample_rate)

        if isinstance(frame, bytes):
            frame = np.frombuffer(frame, dtype=np.int16).astype(np.float64)

        if isinstance(frame, np.ndarray) and frame.dtype == np.int16:
            frame = frame.astype(np.float64)

        expected_len = self.frame_size
        if len(frame) > expected_len:
            frame = frame[:expected_len]
        elif len(frame) < expected_len:
            pad = np.zeros(expected_len - len(frame), dtype=frame.dtype)
            frame = np.concatenate([frame, pad])

        speech = self._is_speech_frame(frame)
        self.speech_buffer.append(speech)

        smoothed = sum(self.speech_buffer) > (len(self.speech_buffer) // 2)

        if smoothed:
            self.hangover = self.hangover_max
        elif self.hangover > 0:
            self.hangover -= 1

        return self.hangover > 0

    def _resample(self, data, orig_sr, target_sr):
        if orig_sr == target_sr:
            return data
        ratio = target_sr / orig_sr
        new_len = int(len(data) * ratio)
        return np.interp(
            np.linspace(0, len(data) - 1, new_len),
            np.arange(len(data)),
            data.astype(np.float64)
        ).astype(data.dtype if isinstance(data, np.ndarray) else np.int16)

    def reset(self):
        self.energy_history.clear()
        self.speech_buffer.clear()
        self.hangover = 0
        self.noise_floor = 0.0

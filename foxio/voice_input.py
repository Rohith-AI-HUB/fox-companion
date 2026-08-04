import numpy as np
import threading
import queue
import wave
import io
import tempfile
import os
from core.logger import get_logger

log = get_logger("voice_input")

SAMPLE_RATE = 16000
CHANNELS = 1


class VoiceInput:
    def __init__(self):
        self._recognizer = None
        self.microphone = True  # Flag indicating mic availability (checked at listen time)
        self.listening = False
        self._thread = None
        self._result_callback = None

    @property
    def recognizer(self):
        """Lazily import SpeechRecognition and create the recognizer on first use."""
        if self._recognizer is None:
            import speech_recognition as sr
            self._recognizer = sr.Recognizer()
            self._recognizer.energy_threshold = 300
            self._recognizer.dynamic_energy_threshold = True
            self._recognizer.pause_threshold = 0.8  # seconds of silence before considering speech complete
            self._recognizer.phrase_threshold = 0.3  # minimum seconds of speaking
        return self._recognizer

    def initialize_microphone(self):
        """Check that a microphone (sounddevice input) is available."""
        import sounddevice as sd
        try:
            devices = sd.query_devices()
            default_input = sd.default.device[0]
            if default_input is None or default_input < 0:
                log.warning("No default input device found")
                self.microphone = False
                return False
            dev_info = sd.query_devices(default_input)
            log.info("Microphone initialized: %s", dev_info.get("name", "unknown"))
            self.microphone = True
            return True
        except Exception as e:
            log.error("Failed to initialize microphone: %s", e)
            self.microphone = False
            return False

    def listen(self, timeout=5.0, on_result=None, on_error=None):
        """
        Listen for voice input and convert to text using sounddevice.

        Args:
            timeout: Maximum seconds to listen before timing out
            on_result: Callback function with transcribed text
            on_error: Callback function with error message
        """
        if self.listening:
            log.warning("Already listening, ignoring new request")
            return

        if not self.microphone:
            if not self.initialize_microphone():
                if on_error:
                    on_error("microphone_error")
                return

        self.listening = True
        self._result_callback = on_result

        # Start listening in background thread
        self._thread = threading.Thread(
            target=self._listen_sync,
            args=(timeout, on_error),
            daemon=True
        )
        self._thread.start()
        log.info("Started listening for voice input (timeout=%.1fs)", timeout)

    def _listen_sync(self, timeout, on_error):
        """Record audio via sounddevice, then recognise with SpeechRecognition."""
        import sounddevice as sd
        import speech_recognition as sr
        try:
            # Record audio with sounddevice
            log.info("Recording audio for up to %.1fs...", timeout)
            frames = int(timeout * SAMPLE_RATE)
            audio_data = sd.rec(
                frames,
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=np.int16,
                blocking=True,
            )
            log.info("Audio captured (%d samples), transcribing...", len(audio_data))

            # Check if audio has any meaningful content (not silence)
            rms = np.sqrt(np.mean(audio_data.astype(np.float64) ** 2))
            if rms < 50:
                log.warning("Audio appears to be silence (RMS=%.1f)", rms)
                if on_error:
                    on_error("timeout")
                return

            # Convert numpy audio to a WAV byte stream for SpeechRecognition
            wav_buffer = io.BytesIO()
            with wave.open(wav_buffer, "wb") as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data.tobytes())
            wav_buffer.seek(0)

            # Use SpeechRecognition to transcribe from WAV data
            with sr.AudioFile(wav_buffer) as source:
                audio = self.recognizer.record(source)

            try:
                text = self.recognizer.recognize_google(audio)
                log.info("Transcription: %s", text)

                if self._result_callback:
                    self._result_callback(text)

            except sr.UnknownValueError:
                log.warning("Speech recognition could not understand audio")
                if on_error:
                    on_error("could_not_understand")

            except sr.RequestError as e:
                log.error("Speech recognition service error: %s", e)
                if on_error:
                    on_error("service_error")

        except Exception as e:
            log.error("Voice input error: %s", e)
            if on_error:
                on_error("unknown_error")

        finally:
            self.listening = False

    def stop(self):
        """Stop current listening session."""
        if self.listening and self._thread:
            self.listening = False
            try:
                import sounddevice as sd
                sd.stop()
            except Exception:
                pass
            log.info("Voice input stopped")

    def is_listening(self):
        """Check if currently listening."""
        return self.listening

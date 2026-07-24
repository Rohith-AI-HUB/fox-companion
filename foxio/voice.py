import asyncio, sys, tempfile, os, threading, time
import miniaudio
from edge_tts.exceptions import NoAudioReceived
from core import config
from core.logger import get_logger

log = get_logger("voice")

if sys.platform.startswith("win"):
    import winsound as _winsound
else:
    _winsound = None

_POLL_INTERVAL_S = 0.05


class VoiceEngine:
    """Text-to-speech engine with interruptible playback and TTS caching.

    Playback is split from synthesis so that ``stop()`` cancels a running
    utterance immediately, even inside the long ``time.sleep(dur)`` that
    the previous implementation used.  A monotonic ``_sequence`` counter
    ensures only the most recently started ``speak()`` call fires its
    ``on_end`` callback — a stopped utterance should not report "done"
    after a newer one has already begun.
    """

    def __init__(self):
        self.muted = False
        self._speaking = False
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self._shutting_down = False
        self._sequence = 0

    # ── Public API ────────────────────────────────────────────────────

    def speak(self, text: str, on_start=None, on_end=None):
        if self._shutting_down or self.muted or not text:
            return
        log.info("speak: %s", text)
        with self._lock:
            self._sequence += 1
            seq = self._sequence
        t = threading.Thread(
            target=self._run, args=(text, on_start, on_end, seq), daemon=True
        )
        t.start()

    def stop(self):
        """Interrupt any current playback immediately."""
        self._stop_event.set()
        if _winsound is not None:
            try:
                _winsound.PlaySound(None, _winsound.SND_PURGE)
            except Exception:
                pass

    def shutdown(self):
        """Idempotent final shutdown: stop playback and refuse new requests."""
        self._shutting_down = True
        self.stop()

    def is_speaking(self) -> bool:
        return self._speaking

    # ── Internal ──────────────────────────────────────────────────────

    def _run(self, text, on_start, on_end, seq):
        # Only one utterance synthesizes at a time, but after releasing the
        # lock a newer call may arrive — we check ``seq`` before signalling.
        with self._lock:
            self._speaking = True
            self._stop_event.clear()
            if on_start:
                on_start(text)

        tmp_mp3 = None
        tmp_wav = None
        try:
            fd, tmp_mp3 = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            synthesized_path = self._synthesize_with_retry(text, tmp_mp3)
            if synthesized_path is None or self._stop_event.is_set():
                return

            pcm = miniaudio.decode_file(synthesized_path)
            dur = pcm.num_frames / pcm.sample_rate

            fd2, tmp_wav = tempfile.mkstemp(suffix=".wav")
            os.close(fd2)
            miniaudio.wav_write_file(tmp_wav, pcm)
            if self._stop_event.is_set():
                return

            self._play_wav_block(tmp_wav, dur)
        except Exception as e:
            log.debug("voice _run suppressed error: %s", e)
        finally:
            for path in (tmp_mp3, tmp_wav):
                if path and os.path.exists(path):
                    try:
                        os.remove(path)
                    except (PermissionError, OSError):
                        pass
            with self._lock:
                if self._sequence == seq:
                    self._speaking = False
            if on_end and self._sequence == seq:
                try:
                    on_end()
                except Exception:
                    pass

    def _synthesize_with_retry(self, text: str, out_path: str):
        for attempt in range(2):
            if self._stop_event.is_set():
                return None
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                try:
                    loop.run_until_complete(self._synthesize(text, out_path))
                finally:
                    loop.close()
                return out_path
            except NoAudioReceived:
                if attempt == 0 and not self._stop_event.is_set():
                    self._sleep_interruptible(1.0)
                    continue
                raise
        return None

    def _play_wav_block(self, wav_path: str, dur: float):
        if _winsound is not None:
            try:
                _winsound.PlaySound(wav_path, _winsound.SND_ASYNC)
            except Exception:
                pass
        # Wait for the declared duration OR until stop event or shorter audio
        self._sleep_interruptible(dur)
        # Ensure sound stops when interrupted or done
        if _winsound is not None:
            try:
                _winsound.PlaySound(None, _winsound.SND_PURGE)
            except Exception:
                pass

    def _sleep_interruptible(self, seconds: float):
        """Sleep ``seconds``, returning immediately after stop is requested."""
        remaining = float(seconds)
        while remaining > 0 and not self._stop_event.is_set():
            chunk = min(_POLL_INTERVAL_S, remaining)
            time.sleep(chunk)
            remaining -= chunk

    async def _synthesize(self, text: str, path: str):
        c = edge_tts.Communicate(text, config.VOICE_NAME, rate=config.VOICE_RATE, pitch=config.VOICE_PITCH)
        await c.save(path)

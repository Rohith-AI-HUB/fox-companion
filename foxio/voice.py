import asyncio, sys, tempfile, os, threading, time, hashlib, shutil
from pathlib import Path
from core import config
from core.logger import get_logger

log = get_logger("voice")

if sys.platform.startswith("win"):
    import winsound as _winsound
else:
    _winsound = None

_POLL_INTERVAL_S = 0.05

_CACHE_DIR = os.path.join(tempfile.gettempdir(), "fox-tts-cache")
_CACHE_MAX_BYTES = 50 * 1024 * 1024  # 50 MB
_CACHE_MAX_AGE_S = 7 * 24 * 60 * 60   # 7 days


def _cache_key(text: str) -> str:
    payload = f"{text}|{config.VOICE_NAME}|{config.VOICE_RATE}|{config.VOICE_PITCH}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _cache_path(key: str) -> str:
    return os.path.join(_CACHE_DIR, key + ".wav")


def _ensure_cache_dir():
    try:
        os.makedirs(_CACHE_DIR, exist_ok=True)
    except OSError:
        pass


def _evict_cache_if_needed():
    """LRU eviction: keep total size under the cap; drop aged entries first."""
    try:
        _ensure_cache_dir()
        entries = []
        total = 0
        now = time.time()
        for name in os.listdir(_CACHE_DIR):
            if not name.endswith(".wav"):
                continue
            p = os.path.join(_CACHE_DIR, name)
            try:
                st = os.stat(p)
            except OSError:
                continue
            age = now - st.st_mtime
            size = st.st_size
            total += size
            entries.append((age, size, p))
        aged_cutoff = now - _CACHE_MAX_AGE_S
        aged = [p for age, size, p in entries if os.path.getmtime(p) < aged_cutoff]
        for p in aged:
            try:
                total -= os.path.getsize(p)
                os.remove(p)
            except OSError:
                pass
        if total <= _CACHE_MAX_BYTES:
            return
        entries.sort(key=lambda x: x[0], reverse=True)
        for age, size, p in entries:
            if total <= _CACHE_MAX_BYTES:
                break
            try:
                total -= size
                os.remove(p)
            except OSError:
                pass
    except OSError as e:
        log.debug("cache eviction skipped: %s", e)


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
        self._last_duration = 0.0
        _ensure_cache_dir()
        try:
            _evict_cache_if_needed()
        except Exception:
            pass

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

    def last_duration(self) -> float:
        """Duration in seconds of the most recently completed (or started) utterance."""
        return self._last_duration

    # ── Internal ──────────────────────────────────────────────────────

    def _run(self, text, on_start, on_end, seq):
        with self._lock:
            self._speaking = True
            self._stop_event.clear()
            if on_start:
                on_start(text)

        tmp_mp3 = None
        managed_wav = None
        is_cached_hit = False
        try:
            import miniaudio
            key = _cache_key(text)
            cached = _cache_path(key)
            if os.path.exists(cached):
                is_cached_hit = True
                managed_wav = cached
                log.debug("tts cache hit for: %s", text[:40])
            else:
                fd, tmp_mp3 = tempfile.mkstemp(suffix=".mp3")
                os.close(fd)
                synthesized_path = self._synthesize_with_retry(text, tmp_mp3)
                if synthesized_path is None or self._stop_event.is_set():
                    return
                pcm = miniaudio.decode_file(synthesized_path)
                self._last_duration = pcm.num_frames / pcm.sample_rate
                fd2, managed_wav = tempfile.mkstemp(suffix=".wav")
                os.close(fd2)
                miniaudio.wav_write_file(managed_wav, pcm)
                try:
                    _ensure_cache_dir()
                    shutil.copyfile(managed_wav, cached)
                    _evict_cache_if_needed()
                except Exception as e:
                    log.debug("tts cache write skipped: %s", e)

            if self._stop_event.is_set():
                return

            if is_cached_hit:
                pcm = miniaudio.decode_file(managed_wav)
                self._last_duration = pcm.num_frames / pcm.sample_rate

            self._play_wav_block(managed_wav, self._last_duration)
        except Exception as e:
            log.debug("voice _run suppressed error: %s", e)
        finally:
            if not is_cached_hit and managed_wav and os.path.exists(managed_wav):
                try:
                    os.remove(managed_wav)
                except (PermissionError, OSError):
                    pass
            if tmp_mp3 and os.path.exists(tmp_mp3):
                try:
                    os.remove(tmp_mp3)
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
        from edge_tts.exceptions import NoAudioReceived
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
        self._sleep_interruptible(dur)
        if _winsound is not None:
            try:
                _winsound.PlaySound(None, _winsound.SND_PURGE)
            except Exception:
                pass

    def _sleep_interruptible(self, seconds: float):
        remaining = float(seconds)
        while remaining > 0 and not self._stop_event.is_set():
            chunk = min(_POLL_INTERVAL_S, remaining)
            time.sleep(chunk)
            remaining -= chunk

    async def _synthesize(self, text: str, path: str):
        import edge_tts
        c = edge_tts.Communicate(text, config.VOICE_NAME, rate=config.VOICE_RATE, pitch=config.VOICE_PITCH)
        await c.save(path)

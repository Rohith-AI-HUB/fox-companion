import asyncio, edge_tts, winsound, tempfile, os, threading, time
import miniaudio
from edge_tts.exceptions import NoAudioReceived
from core import config
from core.logger import get_logger

log = get_logger("voice")

class VoiceEngine:
    def __init__(self):
        self.muted = False
        self._speaking = False
        self._lock = threading.Lock()

    def speak(self, text: str, on_start=None, on_end=None):
        if self.muted or not text:
            return
        log.info("speak: %s", text)
        t = threading.Thread(target=self._run, args=(text, on_start, on_end), daemon=True)
        t.start()

    def stop(self):
        winsound.PlaySound(None, winsound.SND_PURGE)

    def _run(self, text, on_start, on_end):
        with self._lock:
            self._speaking = True
            if on_start:
                on_start(text)
            tmp_mp3 = None
            tmp_wav = None
            try:
                fd, tmp_mp3 = tempfile.mkstemp(suffix=".mp3")
                os.close(fd)
                for attempt in range(2):
                    try:
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            loop.run_until_complete(self._synthesize(text, tmp_mp3))
                        finally:
                            loop.close()
                        break
                    except NoAudioReceived:
                        if attempt == 0:
                            time.sleep(1)
                            continue
                        raise
                pcm = miniaudio.decode_file(tmp_mp3)
                dur = pcm.num_frames / pcm.sample_rate
                fd2, tmp_wav = tempfile.mkstemp(suffix=".wav")
                os.close(fd2)
                miniaudio.wav_write_file(tmp_wav, pcm)
                winsound.PlaySound(tmp_wav, winsound.SND_ASYNC)
                time.sleep(dur)
            except Exception:
                pass
            finally:
                self._speaking = False
                if tmp_mp3 and os.path.exists(tmp_mp3):
                    try:
                        os.remove(tmp_mp3)
                    except PermissionError:
                        pass
                if tmp_wav and os.path.exists(tmp_wav):
                    try:
                        os.remove(tmp_wav)
                    except PermissionError:
                        pass
                if on_end:
                    on_end()

    async def _synthesize(self, text: str, path: str):
        c = edge_tts.Communicate(text, config.VOICE_NAME, rate=config.VOICE_RATE, pitch=config.VOICE_PITCH)
        await c.save(path)

    def is_speaking(self) -> bool:
        return self._speaking

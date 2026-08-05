"""Tests for the synchronized ``on_listening`` activation hook.

Verifies the audio cue fires exactly when the listening state becomes active:
synchronously (well inside the 100 ms requirement), and never on paths where
listening did not actually start (already listening / microphone unavailable).
"""
import time

from foxio.voice_input import VoiceInput


def _make_input(microphone=True):
    inp = VoiceInput.__new__(VoiceInput)  # skip __init__ (no recognizer/queue)
    inp._recognizer = None
    inp.microphone = microphone
    inp.listening = False
    inp._thread = None
    inp._result_callback = None
    return inp


def test_on_listening_fires_within_100ms_of_state_active(monkeypatch):
    inp = _make_input()
    monkeypatch.setattr(VoiceInput, "_listen_sync",
                        lambda self, timeout, on_error: None)

    fired = []
    t0 = time.perf_counter()

    def hook():
        fired.append((time.perf_counter() - t0, inp.listening))

    inp.listen(timeout=3.0, on_listening=hook)

    assert len(fired) == 1
    delta, listening_at_hook = fired[0]
    assert listening_at_hook is True  # listening state already active
    assert delta < 0.1                # activated within 100 ms


def test_on_listening_not_fired_when_already_listening(monkeypatch):
    inp = _make_input()
    inp.listening = True
    fired = []
    inp.listen(timeout=3.0, on_listening=lambda: fired.append(True))
    assert fired == []


def test_on_listening_not_fired_when_mic_unavailable(monkeypatch):
    inp = _make_input(microphone=False)
    monkeypatch.setattr(inp, "initialize_microphone", lambda: False)
    errors = []
    fired = []
    inp.listen(timeout=3.0, on_error=errors.append,
               on_listening=lambda: fired.append(True))
    assert fired == []
    assert errors == ["microphone_error"]


def test_on_listening_failure_does_not_block_listening(monkeypatch):
    inp = _make_input()
    started = []

    def fake_listen(self, timeout, on_error):
        started.append(True)

    monkeypatch.setattr(VoiceInput, "_listen_sync", fake_listen)

    def boom():
        raise RuntimeError("audio engine down")

    inp.listen(timeout=3.0, on_listening=boom)

    assert inp.listening is True
    assert started == [True]  # recording thread still started

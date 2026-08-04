"""End-to-end validation of the fixed wake listener.

Feeds real synthesized audio through WakeListener._process_audio (the exact
runtime code path) and reports triggers across scenarios:
  - both wake-word variants ('Hey Fox', 'Fox')
  - distance (amplitude scaling), noisy acoustic environments (SNR)
  - accent diversity (multiple edge-tts voices, including voices held out of training)
  - control phrases (must NOT trigger)
"""
import asyncio
import io
import os
import sys
import tempfile
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edge_tts
import miniaudio
from scipy import signal as sp_signal

from foxio.wake_listener import WakeListener, CHUNK_SIZE

SR = 16000

VOICES = [
    "en-IN-NeerjaNeural",
    "en-IN-PrabhatNeural",
    "en-US-JennyNeural",
    "en-US-GuyNeural",
    "en-GB-SoniaNeural",
    "en-AU-NatashaNeural",
]

POSITIVE = ["Hey Fox", "Fox"]
CONTROLS = ["Hello there", "What is the weather today", "Good morning", "Thank you very much"]


async def synth(text: str, voice: str, rate="+0%") -> np.ndarray:
    c = edge_tts.Communicate(text, voice, rate=rate)
    buf = io.BytesIO()
    async for chunk in c.stream():
        if chunk["type"] == "audio":
            buf.write(chunk["data"])
    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(buf.getvalue())
        tmp = f.name
    try:
        pcm = miniaudio.decode_file(tmp)
    finally:
        os.unlink(tmp)
    x = np.frombuffer(pcm.samples, dtype=np.int16).astype(np.float64)
    x = sp_signal.resample_poly(x, SR, pcm.sample_rate)
    return np.clip(x, -32768, 32767).astype(np.int16)


def add_noise(data, snr_db, seed):
    rng = np.random.default_rng(seed)
    sig_pow = np.mean(data.astype(np.float64) ** 2)
    noise = rng.standard_normal(len(data)) * np.sqrt(sig_pow / (10 ** (snr_db / 10)))
    return np.clip(data.astype(np.float64) + noise, -32768, 32767).astype(np.int16)


def make_scenarios(clip):
    return {
        "near": clip,
        "mid": (clip.astype(np.float64) * 0.5).astype(np.int16),
        "far": (clip.astype(np.float64) * 0.25).astype(np.int16),
        "noise20": add_noise(clip, 20, 1),
        "noise12": add_noise(clip, 12, 2),
    }


def run_clip(wl, clip):
    """Feed one clip and return whether it triggered (tracking via monkeypatch-free count)."""
    wl._triggered_this_clip = False

    def _on_wake():
        wl._triggered_this_clip = True

    original = wl.on_wake
    wl.on_wake = _on_wake
    silence = np.zeros(SR, dtype=np.int16)
    for data in (silence, clip):
        for i in range(0, len(data) - CHUNK_SIZE, CHUNK_SIZE):
            wl.audio_queue.put(data[i:i + CHUNK_SIZE].reshape(-1, 1))
            wl._process_audio()
    # soft-reset detector state so the next clip starts clean (as a real
    # utterance boundary would)
    wl._reset_detector()
    wl._last_trigger_at = 0.0
    wl.on_wake = original
    return wl._triggered_this_clip


async def main():
    wl = WakeListener(on_wake=None)  # uses fox_verifier.pkl
    if not wl._load_detector() or wl.verifier is None:
        print("FAIL: verifier not loaded — train_verifier.py must be run first")
        return
    print(f"verifier loaded; threshold={wl.wake_threshold:.3f}")

    clips = {}
    for voice in VOICES:
        for text in POSITIVE + CONTROLS:
            key = (voice, text)
            clips[key] = await synth(text, voice)
            print(f"  synth {voice} | {text}")

    print("\n=== DETECTION RATE BY SCENARIO (positive clips) ===")
    scenario_stats = {}
    for sc in ["near", "mid", "far", "noise20", "noise12"]:
        hits = 0
        total = 0
        for voice in VOICES:
            for text in POSITIVE:
                variants = make_scenarios(clips[(voice, text)])
                triggered = run_clip(wl, variants[sc])
                hits += int(triggered)
                total += 1
        scenario_stats[sc] = (hits, total)
        print(f"  {sc:<8} {hits}/{total} detected")

    print("\n=== BY WAKE VARIANT (near) ===")
    for text in POSITIVE:
        hits = sum(run_clip(wl, make_scenarios(clips[(v, text)])["near"]) for v in VOICES)
        print(f"  '{text}': {hits}/{len(VOICES)}")

    print("\n=== BY VOICE (near) ===")
    for voice in VOICES:
        hits = sum(run_clip(wl, make_scenarios(clips[(voice, t)])["near"]) for t in POSITIVE)
        print(f"  {voice:<22} {hits}/{len(POSITIVE)}")

    print("\n=== FALSE POSITIVES (control phrases, all scenarios) ===")
    fp = 0
    total = 0
    for voice in VOICES:
        for text in CONTROLS:
            for variant in make_scenarios(clips[(voice, text)]).values():
                fp += int(run_clip(wl, variant))
                total += 1
    print(f"  {fp}/{total} control clips falsely triggered")
    ok = all(h == t for h, t in scenario_stats.values()) and fp == 0
    print("\nRESULT:", "PASS — all scenarios detected, no false positives" if ok
          else "PARTIAL/FAIL — see above")


if __name__ == "__main__":
    asyncio.run(main())

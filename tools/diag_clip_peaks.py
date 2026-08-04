"""Measure per-clip PEAK wake scores for positives vs control phrases.

Feeds clips through the real runtime scoring path (WakeListener._process_audio)
and records the maximum verifier score observed. This is the operating-point
data needed to pick a threshold that separates true wake words from controls.
Reuses the training TTS cache so nothing is re-synthesized.
"""
import hashlib
import os
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxio.wake_listener import WakeListener, CHUNK_SIZE

SR = 16000
CACHE = os.path.join(tempfile.gettempdir(), "fox-verifier-cache")

VOICES = ["en-IN-NeerjaNeural", "en-IN-PrabhatNeural", "en-US-JennyNeural",
          "en-US-GuyNeural", "en-GB-SoniaNeural", "en-AU-NatashaNeural"]
POSITIVE = ["Hey Fox", "Fox"]
CONTROLS = ["Hello there", "What is the weather today", "Good morning",
            "Thank you very much"]


def load_clip(voice, text):
    key = hashlib.sha1(f"{voice}|+0%|{text}".encode("utf-8")).hexdigest()[:16]
    return np.load(os.path.join(CACHE, key + ".npy"))


def add_noise(data, snr_db, seed):
    rng = np.random.default_rng(seed)
    sig_pow = np.mean(data.astype(np.float64) ** 2)
    noise = rng.standard_normal(len(data)) * np.sqrt(sig_pow / (10 ** (snr_db / 10)))
    return np.clip(data.astype(np.float64) + noise, -32768, 32767).astype(np.int16)


def make_scenarios(clip):
    return {"near": clip,
            "mid": (clip.astype(np.float64) * 0.5).astype(np.int16),
            "far": (clip.astype(np.float64) * 0.25).astype(np.int16),
            "noise20": add_noise(clip, 20, 1),
            "noise12": add_noise(clip, 12, 2)}


def peak_score(wl, clip):
    """Feed the clip and return the peak verifier score observed."""
    wl.verifier_peak = 0.0
    orig = wl._score_with_verifier
    def wrapped(chunk):
        s = orig(chunk)
        wl.verifier_peak = max(wl.verifier_peak, s)
        return s
    wl._score_with_verifier = wrapped
    silence = np.zeros(SR, dtype=np.int16)
    for data in (silence, clip):
        for i in range(0, len(data) - CHUNK_SIZE, CHUNK_SIZE):
            wl.audio_queue.put(data[i:i + CHUNK_SIZE].reshape(-1, 1))
            wl._process_audio()
    wl._score_with_verifier = orig
    wl._reset_detector()
    wl._last_trigger_at = 0.0
    return wl.verifier_peak


def main():
    wl = WakeListener(on_wake=None)
    if not wl._load_detector() or wl.verifier is None:
        print("FAIL: verifier not loaded")
        return

    print("\n=== PEAK SCORES: positives by scenario ===")
    pos_by_sc = {k: [] for k in ["near", "mid", "far", "noise20", "noise12"]}
    for v in VOICES:
        for t in POSITIVE:
            for sc, clip in make_scenarios(load_clip(v, t)).items():
                pos_by_sc[sc].append(peak_score(wl, clip))
    for sc, scores in pos_by_sc.items():
        print(f"  {sc:<8} min={min(scores):.3f} max={max(scores):.3f} median={float(np.median(scores)):.3f}")

    print("\n=== PEAK SCORES: controls by phrase (min/max across voices+scenarios) ===")
    ctl_by_phrase = {}
    for v in VOICES:
        for t in CONTROLS:
            ctl_by_phrase.setdefault(t, [])
            for sc, clip in make_scenarios(load_clip(v, t)).items():
                ctl_by_phrase[t].append(peak_score(wl, clip))
    all_ctrl = []
    for t, scores in ctl_by_phrase.items():
        all_ctrl += scores
        print(f"  {t:<28} min={min(scores):.3f} max={max(scores):.3f}")

    print("\n=== PEAK SCORES: controls by voice (min/max across phrases+scenarios) ===")
    for v in VOICES:
        s = []
        for t in CONTROLS:
            for sc, clip in make_scenarios(load_clip(v, t)).items():
                s.append(peak_score(wl, clip))
        print(f"  {v:<22} min={min(s):.3f} max={max(s):.3f}")

    print("\n=== THRESHOLD SWEEP (positives: MUST all exceed T; controls: MUST all be <= T) ===")
    all_pos = [x for sc in pos_by_sc.values() for x in sc]
    all_ctrl = []
    for t in CONTROLS:
        all_ctrl += ctl_by_phrase[t]
    print(f"  positives n={len(all_pos)} min={min(all_pos):.3f} max={max(all_pos):.3f}")
    print(f"  controls  n={len(all_ctrl)} min={min(all_ctrl):.3f} max={max(all_ctrl):.3f}")
    ok = min(all_pos) > max(all_ctrl)
    print(f"  clean separation possible: {ok}")
    if ok:
        lo, hi = max(all_ctrl), min(all_pos)
        print(f"  -> any threshold in ({lo:.3f}, {hi:.3f}) works; midpoint={round((lo + hi) / 2, 3)}")

    print("\n  T         detect%% (pos>T)   false-trigger%% (ctl>T)")
    for T in [0.50, 0.55, 0.60, 0.65, 0.70, 0.75]:
        det = sum(1 for x in all_pos if x > T) / len(all_pos)
        fp = sum(1 for x in all_ctrl if x > T) / len(all_ctrl)
        print(f"  {T:<8.2f}  {det*100:6.1f}%          {fp*100:6.1f}%")


if __name__ == "__main__":
    main()
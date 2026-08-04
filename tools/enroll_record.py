"""Record the user's real voice for wake-word enrollment.

Runs interactively: for each phrase it shows a short countdown, then records
seconds of audio from the microphone, trims leading/trailing silence, and saves
a 16 kHz mono int16 clip under assets/enroll/ as pos_*.npy (wake words) or
neg_*.npy (control phrases spoken by the user).

Training (tools/train_verifier.py) picks these up automatically.
"""
import os
import re
import sys
import time

import numpy as np
import sounddevice as sd

SR = 16000
OUT_DIR = os.path.join("assets", "enroll")
RECORD_SECONDS = 3.0

# (phrase, label). pos_* clips are wake words; neg_* clips are phrases the
# user actually says that must NOT wake the companion.
PHRASES = [
    ("Hey Fox", "pos"),
    ("Fox", "pos"),
    ("Hey Fox", "pos"),
    ("Fox", "pos"),
    ("Good morning", "neg"),
    ("Hello there", "neg"),
]

REP_PER_FRAME = int(SR * 0.020)  # 20 ms frames for silence trim


def record(seconds: float) -> np.ndarray:
    n = int(SR * seconds)
    data = sd.rec(n, samplerate=SR, channels=1, dtype="int16")
    sd.wait()
    return data.ravel()


def trim(clip: np.ndarray, thr: float = 0.02, pad_s: float = 0.05) -> np.ndarray:
    n = len(clip) // REP_PER_FRAME
    if n == 0:
        return clip
    rms = np.array([
        float(np.sqrt(np.mean(
            clip[i * REP_PER_FRAME:(i + 1) * REP_PER_FRAME].astype(np.float64) ** 2)))
        for i in range(n)
    ])
    peak = max(rms.max(), 1.0)
    on = rms > peak * thr
    if not on.any():
        return clip
    idx = np.where(on)[0]
    s = max(0, idx[0] - int(pad_s / 0.020))
    e = min(n, idx[-1] + int(pad_s / 0.020) + 1)
    return clip[s * REP_PER_FRAME:e * REP_PER_FRAME]


def sanitize(phrase: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", phrase).strip("_")


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    print(f"Enrollment -> {os.path.join(os.getcwd(), OUT_DIR)}")
    print("Speak naturally and clearly. Both 'Hey Fox' wake variants are recorded,")
    print("plus two control phrases so the verifier learns NOT to wake on them.\n")

    for i, (phrase, label) in enumerate(PHRASES):
        stem = f"{label}_{sanitize(phrase)}"
        print(f"\n[{i + 1}/{len(PHRASES)}] '{phrase}' ({label})")
        for c in (3, 2, 1):
            print(f"  speak in {c}...", flush=True)
            time.sleep(1.0)
        print("  RECORDING", flush=True)
        raw = record(RECORD_SECONDS)
        clip = trim(raw)
        rms = float(np.sqrt(np.mean(clip.astype(np.float64) ** 2)))
        path = os.path.join(OUT_DIR, f"{stem}_{int(time.time())}.npy")
        np.save(path, clip)
        print(f"  saved {os.path.basename(path)}  ({len(clip) / SR:.2f}s, rms={rms:.0f})")
        # small gap so the mic clears between takes
        time.sleep(0.6)

    npos = len(os.listdir(OUT_DIR) and [f for f in os.listdir(OUT_DIR) if f.startswith("pos_")])
    nneg = len([f for f in os.listdir(OUT_DIR) if f.startswith("neg_")])
    print(f"\nDone: {npos} positive (wake) + {nneg} negative (control) clips in {OUT_DIR}")
    print("Now re-run: python tools\\train_verifier.py  to retrain with your voice.")


if __name__ == "__main__":
    main()
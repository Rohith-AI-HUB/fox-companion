"""Train a corrected 'Hey Fox' / 'Fox' custom verifier for the wake listener.

Replaces the defective fox.onnx with a sklearn LogisticRegression verifier
trained on openwakeword audio-embedding features (22 frames x 96 dims), using
the exact recipe from openwakeword.custom_verifier_model.train_verifier_model.

Training data: edge-tts synthesized speech, multiple voices (accent diversity),
with mild distance (amplitude) and noise augmentation. Validation is done on
held-out voices to measure accent generalization.

Model-selection: the deployment threshold is chosen to separate FOX-ending
windows from CLEAN (fox-free, non-rhyme) negative windows — i.e. the false
triggers the runtime actually cares about. Acoustically fox-like rhyme words
("socks", "box", …) are included in TRAINING as hard negatives but excluded
from the threshold decision, since they are physically near-duplicates of
/fɒks/ and would dominate any strict separation.
"""
import asyncio
import hashlib
import io
import os
import pickle
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import edge_tts
import miniaudio
from scipy import signal as sp_signal
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import FunctionTransformer, StandardScaler

from openwakeword.utils import AudioFeatures
from foxio.wake_listener import flatten_features

SR = 16000
CHUNK = 1280
# Short scoring window (must match foxio.wake_listener.N_FEATURE_FRAMES):
# ~0.8s covering the final word, where the wake word always lives.
N_FEATURE_FRAMES = 10

OUT_PATH = os.path.join("assets", "wake", "fox_verifier.pkl")

# Wide accent diversity: 14 training voices, 4 held-out voices for validation.
TRAIN_VOICES = [
    "en-IN-NeerjaNeural",   # Indian English (female)
    "en-IN-PrabhatNeural",  # Indian English (male)
    "en-US-JennyNeural",    # US English (female)
    "en-US-GuyNeural",      # US English (male)
    "en-US-AriaNeural",     # US English (female, different)
    "en-GB-RyanNeural",     # British English (male)
    "en-GB-LibbyNeural",    # British English (female)
    "en-AU-WilliamNeural",  # Australian English (male)
    "en-CA-ClaraNeural",    # Canadian English (female)
    "en-IE-EmilyNeural",    # Irish English (female)
    "en-US-AvaNeural",      # US English (female)
    "en-CA-LiamNeural",     # Canadian English (male)
    "en-IE-ConnorNeural",   # Irish English (male)
    "en-SG-LunaNeural",     # Singapore English (female)
]

VAL_VOICES = [
    "en-GB-SoniaNeural",    # British English (female)
    "en-AU-NatashaNeural",  # Australian English (female)
    "en-US-AnaNeural",      # US English (female)
    "en-KE-AsiliaNeural",   # Kenyan English (female) — new accent family
]

POSITIVE_PHRASES = [
    "Hey Fox",
    "Hey Fox!",
    "Fox",
    "Fox!",
    "Fox Fox",
    "Fox Fox Fox",
    "Oh Fox",
    "Hey, Fox",
    "Hey Fox, hey",
    "come here, Fox",
    "wake up, Fox",
    "Listen, Fox",
]

# CLEAN negatives: no word "fox", no fox-rhyme ending. These are the false
# triggers the runtime must reject, and they drive the threshold decision.
# No phrase here may contain the word "fox", because collect_clip_windows
# labels every window of a negative clip as negative and a mid-sentence fox
# window would be a structurally-positive sample.
CLEAN_NEGATIVE_PHRASES = [
    "Hello there",
    "Good morning",
    "What is the weather today",
    "Tell me a joke",
    "How are you doing",
    "Thank you very much",
    "Good night",
    "What time is it",
    "I am going to work now",
    "Can you help me",
]

# RHYME negatives: words that end like /fɒks/ (socks, box, clock, six). These
# are acoustically near-duplicates of "fox" and are hard to separate, but they
# make the verifier precise about the exact /f-ɒ-k-s/ onset. Used in training
# only; excluded from the threshold decision.
RHYME_NEGATIVE_PHRASES = [
    "I need some socks",
    "the clock is ticking",
    "I locked the door",
    "there is a box",
    "count to six",
]

# Speech-rate variation adds little vs. voice diversity; keep one rate so the
# retrain loop is fast (the +0% cache is already populated for most voices).
RATES = ["+0%"]


async def synth(text: str, voice: str, rate: str) -> np.ndarray:
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


def trim_silence(data: np.ndarray, floor: float = 0.02) -> np.ndarray:
    """Trim leading/trailing silence using chunk RMS relative to peak."""
    rms = []
    for i in range(0, len(data) - CHUNK, CHUNK):
        c = data[i:i + CHUNK].astype(np.float64)
        rms.append(np.sqrt(np.mean(c ** 2)))
    if not rms:
        return data
    peak = max(max(rms), 1.0)
    first = next((i for i, r in enumerate(rms) if r > peak * floor), 0)
    last = next((i for i in range(len(rms) - 1, -1, -1) if rms[i] > peak * floor), len(rms) - 1)
    return data[max(0, first * CHUNK):min(len(data), (last + 1) * CHUNK)]


def add_noise(data: np.ndarray, snr_db: float, seed: int) -> np.ndarray:
    rng = np.random.default_rng(seed)
    sig_pow = np.mean(data.astype(np.float64) ** 2)
    noise_pow = sig_pow / (10 ** (snr_db / 10))
    noise = (rng.standard_normal(len(data)) * np.sqrt(noise_pow))
    return np.clip(data.astype(np.float64) + noise, -32768, 32767).astype(np.int16)


def augment(clip: np.ndarray, idx: int):
    """Return list of (name, audio) variants: distance scaling + mild noise.

    Distance (amplitude) and mild-noise invariance only. Heavy noise blurred
    speech and made a logistic verifier fire on any voiced input, destroying
    precision, so no aggressive SNR levels (beyond a mild noise12) are used.
    """
    clip64 = clip.astype(np.float64)
    variants = [("near", clip)]
    variants.append(("mid", np.clip(clip64 * 0.5, -32768, 32767).astype(np.int16)))
    variants.append(("far", np.clip(clip64 * 0.25, -32768, 32767).astype(np.int16)))
    variants.append(("noise20", add_noise(clip, 20, seed=idx)))
    variants.append(("noise12", add_noise(clip, 12, seed=idx + 999)))
    return variants


def collect_clip_windows(preprocessor, variants, speech_gate: bool, label: str):
    """Extract 22-frame windows for all variants of one clip in a single pass.

    The runtime scorer always evaluates the *last* 22 features (the window that
    ends at the current audio chunk). For positive clips the wake word is the
    final content of the clip, so only windows ending at the last few chunks are
    captured — these are exactly the windows the runtime sees right after the
    user says the wake word. For negative clips every clip window is captured.

    One ``reset()`` per clip (resets are expensive in openwakeword); variants
    are separated by a silence gap long enough to fully flush the feature
    buffer so each variant's windows are clean.
    """
    preprocessor.reset()
    results = []
    gap = np.zeros(int(SR * 2.5), dtype=np.int16)
    for name, data in variants:
        windows = []
        clip_rms = []
        n_chunks = (len(data) - CHUNK) // CHUNK + 1
        for i in range(0, len(gap) - CHUNK, CHUNK):
            preprocessor(gap[i:i + CHUNK].astype(np.float32))
        for i in range(0, len(data) - CHUNK, CHUNK):
            chunk = data[i:i + CHUNK].astype(np.float32)
            clip_rms.append(float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2))))
            preprocessor(chunk)
            if len(preprocessor.feature_buffer) >= N_FEATURE_FRAMES:
                k = i // CHUNK
                if speech_gate:
                    # Wake word is the final content of each positive phrase:
                    # keep only windows ending in the last 6 chunks of the clip.
                    if k < n_chunks - 6:
                        continue
                    peak = max(clip_rms) if clip_rms else 0.0
                    tail = clip_rms[max(0, k - 2):k + 1]
                    # relative gate only — an absolute floor would drop quiet
                    # (far-field) positive variants entirely
                    if max(tail) <= peak * 0.05:
                        continue
                windows.append(preprocessor.get_features(N_FEATURE_FRAMES)[0])
        arr = np.array(windows) if windows else np.empty((0, N_FEATURE_FRAMES, 96))
        if arr.shape[0] == 0:
            print(f"    [warn] {label}/{name}: no windows collected")
        results.append((name, arr))
    return results


async def main():
    t0 = time.time()
    cache_dir = os.path.join(tempfile.gettempdir(), "fox-verifier-cache")
    os.makedirs(cache_dir, exist_ok=True)

    preprocessor = AudioFeatures(inference_framework="onnx")

    train_pos, train_neg = [], []
    val_pos, val_neg_clean, val_neg_rhyme = [], [], []

    async def synth_cached(text, voice, rate, attempts=5):
        raw = f"{voice}|{rate}|{text}"
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        path = os.path.join(cache_dir, key + ".npy")
        if os.path.exists(path):
            return np.load(path)
        last = None
        for i in range(attempts):
            try:
                data = await synth(text, voice, rate)
                np.save(path, data)
                return data
            except Exception as e:
                last = e
                print(f"    [retry {i + 1}/{attempts}] {voice}: {text!r}: {e}")
                await asyncio.sleep(2 ** i)
        raise last

    # Phase 0: pre-download every clip to cache (with retries) so the slow
    # feature-collection phase below never touches the network.
    all_neg = CLEAN_NEGATIVE_PHRASES + RHYME_NEGATIVE_PHRASES
    print("phase 0: pre-downloading all clips...")
    for voice in TRAIN_VOICES + VAL_VOICES:
        for rate in RATES:
            for phrase in POSITIVE_PHRASES + all_neg:
                await synth_cached(phrase, voice, rate)
    print("phase 0 done: all clips cached\n")

    def load_cached(voice, phrase):
        raw = f"{voice}|+0%|{phrase}"
        key = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
        return np.load(os.path.join(cache_dir, key + ".npy"))

    idx = 0
    for voice in TRAIN_VOICES:
        for phrase in POSITIVE_PHRASES:
            clip = load_cached(voice, phrase)
            clip = trim_silence(clip)
            for vname, w in collect_clip_windows(preprocessor, augment(clip, idx), True, f"train-pos-{voice}-{phrase}"):
                if w.shape[0]:
                    train_pos.append(w)
            idx += 1
        for phrase in all_neg:
            clip = load_cached(voice, phrase)
            clip = trim_silence(clip)
            for vname, w in collect_clip_windows(preprocessor, augment(clip, idx), False, f"train-neg-{voice}-{phrase}"):
                if w.shape[0]:
                    train_neg.append(w)
            idx += 1

    idx = 0
    for voice in VAL_VOICES:
        for phrase in POSITIVE_PHRASES:
            clip = load_cached(voice, phrase)
            clip = trim_silence(clip)
            for vname, w in collect_clip_windows(preprocessor, augment(clip, idx), True, f"val-pos-{voice}-{phrase}"):
                if w.shape[0]:
                    val_pos.append(w)
            idx += 1
        for phrase in CLEAN_NEGATIVE_PHRASES:
            clip = load_cached(voice, phrase)
            clip = trim_silence(clip)
            for vname, w in collect_clip_windows(preprocessor, augment(clip, idx), False, f"val-neg-clean-{voice}-{phrase}"):
                if w.shape[0]:
                    val_neg_clean.append(w)
            idx += 1
        for phrase in RHYME_NEGATIVE_PHRASES:
            clip = load_cached(voice, phrase)
            clip = trim_silence(clip)
            for vname, w in collect_clip_windows(preprocessor, augment(clip, idx), False, f"val-neg-rhyme-{voice}-{phrase}"):
                if w.shape[0]:
                    val_neg_rhyme.append(w)
            idx += 1

    # ── Enrolled real-voice clips (assets/enroll) ──
    # pos_*.npy = the wake words spoken by the actual user; neg_*.npy = control
    # phrases the user actually says that must NOT wake. These anchor the
    # verifier to the real user's voice so live wake works.
    enroll_dir = os.path.join("assets", "enroll")
    if os.path.isdir(enroll_dir):
        files = sorted(os.listdir(enroll_dir))
        epos = [f for f in files if f.startswith("pos_") and f.endswith(".npy")]
        eneg = [f for f in files if f.startswith("neg_") and f.endswith(".npy")]
        if epos or eneg:
            print(f"enrollment: {len(epos)} real pos + {len(eneg)} real neg clips")
        for f in epos:
            clip = np.load(os.path.join(enroll_dir, f))
            for vname, w in collect_clip_windows(preprocessor, augment(clip, idx), True, f"enroll-pos-{f}"):
                if w.shape[0]:
                    train_pos.append(w)
            idx += 1
        for f in eneg:
            clip = np.load(os.path.join(enroll_dir, f))
            for vname, w in collect_clip_windows(preprocessor, augment(clip, idx), False, f"enroll-neg-{f}"):
                if w.shape[0]:
                    train_neg.append(w)
            idx += 1

    X_tr = np.vstack(train_pos + train_neg)
    y_tr = np.array([1] * sum(len(w) for w in train_pos) + [0] * sum(len(w) for w in train_neg))
    X_va_pos = np.vstack(val_pos)
    X_va_clean = np.vstack(val_neg_clean)
    X_va_rhyme = np.vstack(val_neg_rhyme)

    print(f"\nTrain: {y_tr.sum()} pos, {len(y_tr) - y_tr.sum()} neg | "
          f"Val: {len(val_pos)} pos, {len(val_neg_clean)} clean-neg, {len(val_neg_rhyme)} rhyme-neg  "
          f"({time.time() - t0:.0f}s)")

    best = None
    for C in [0.0001, 0.001, 0.01, 0.1, 1.0]:
        clf = make_pipeline(FunctionTransformer(flatten_features), StandardScaler(),
                            LogisticRegression(random_state=0, max_iter=5000, C=C))
        clf.fit(X_tr, y_tr)
        p_pos = clf.predict_proba(X_va_pos)[:, 1]
        p_clean = clf.predict_proba(X_va_clean)[:, 1]
        p_rhyme = clf.predict_proba(X_va_rhyme)[:, 1]
        sep = p_pos.min() - p_clean.max()
        print(f"  C={C:<7} val min-pos={p_pos.min():.4f} val max-clean={p_clean.max():.4f} "
              f"[max-rhyme={p_rhyme.max():.4f}] clean-sep={sep:+.4f}")
        if best is None or sep > best[0]:
            best = (sep, C, clf, p_pos, p_clean, p_rhyme)

    sep, C, clf, p_pos, p_clean, p_rhyme = best
    lo, hi = float(p_clean.max()), float(p_pos.min())
    suggested = round((lo + hi) / 2, 3)
    print(f"\nBest C={C}: val max-clean={lo:.4f}, val min-pos={hi:.4f} -> suggested threshold={suggested}")
    print(f"  (for reference) rhyme max at this threshold: {(p_rhyme > suggested).sum()}/{len(p_rhyme)} windows")

    os.makedirs(os.path.dirname(OUT_PATH), exist_ok=True)
    with open(OUT_PATH, "wb") as f:
        pickle.dump(clf, f)
    print(f"\nSaved verifier -> {OUT_PATH}  ({os.path.getsize(OUT_PATH)} bytes)")


if __name__ == "__main__":
    asyncio.run(main())
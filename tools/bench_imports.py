"""Benchmark module import/load time (startup proxy) and hot-path timings.

Usage:
    python tools/bench_imports.py [--hot]
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

MODULES = [
    "core.config",
    "core.logger",
    "core.easing",
    "core.dialogue",
    "core.brain",
    "core.physics",
    "core.behavior",
    "foxio.wake_listener",
    "foxio.voice",
    "foxio.voice_input",
    "foxio.fox_brain_llm",
    "foxio.vad",
    "foxio.screen_watcher",
    "foxio.screen_reader",
    "brain.brain",
    "ui.window",
    "ui.speech_bubble",
    "ui.sprite_manager",
    "ui.particles",
    "ui.chat_input",
    "ui.road_strip",
    "ui.onboarding",
]


def bench_imports():
    total = 0.0
    print(f"{'module':22} {'seconds':>9}")
    print("-" * 33)
    for mod in MODULES:
        t0 = time.perf_counter()
        try:
            __import__(mod)
            dt = time.perf_counter() - t0
            total += dt
            print(f"{mod:22} {dt:9.4f}")
        except Exception as exc:  # noqa: BLE001 - report any import failure
            print(f"{mod:22} ERROR: {exc}")
    print("-" * 33)
    print(f"{'TOTAL':22} {total:9.4f}")
    return total


def bench_hot():
    """Hot-path micro-benchmarks for pure-logic modules."""
    import numpy as np

    from core.physics import PhysicsState
    from foxio import screen_reader as sr
    from brain import brain as bb

    results = {}

    # Physics.step: 10k ticks with walking + jump (no GUI needed)
    ps = PhysicsState(x=0.0, y=500.0, anchor_y=500.0, left=0.0, right=1000.0)
    ps.walk_to(300.0)
    ps.jump(vy=-300.0)
    t0 = time.perf_counter()
    dt = 1.0 / 60.0
    for _ in range(10000):
        ps.step(dt)
    results["physics.step x10k"] = time.perf_counter() - t0

    # Coarse signature: full-1080p image downscale + diff (screen reader gate)
    img = np.random.randint(0, 255, size=(1080, 1920, 3), dtype=np.uint8)
    from PIL import Image

    pil = Image.fromarray(img, mode="RGB")
    t0 = time.perf_counter()
    sig = sr.ScreenReader._coarse_signature(pil)
    for _ in range(200):
        sig2 = sr.ScreenReader._coarse_signature(pil)
        _ = int(np.abs(sig.astype(int) - sig2.astype(int)).sum())
    results["screen sig diff x200"] = time.perf_counter() - t0

    # numpy cosine over stored embeddings (semantic recall core)
    rng = np.random.default_rng(0)
    X = rng.normal(size=(2000, 384)).astype(np.float32)
    q = rng.normal(size=384).astype(np.float32)
    qn = q / np.linalg.norm(q)
    Xn = X / np.linalg.norm(X, axis=1, keepdims=True)
    t0 = time.perf_counter()
    for _ in range(200):
        scores = Xn @ qn
        _ = np.argsort(-scores)[:5]
    results["semantic top-5 x200"] = time.perf_counter() - t0

    for name, t in results.items():
        print(f"{name:28} {t:9.4f}s")
    return results


def main():
    mode = "--hot" in sys.argv
    bench_imports()
    if mode:
        print()
        bench_hot()


if __name__ == "__main__":
    main()

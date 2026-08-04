"""Live microphone wake-word test (rev 2).

Runs the REAL WakeListener and, by wrapping its scoring call, reports every
second: the live audio loudness (RMS) and the highest verifier score seen so
far. This distinguishes "mic not capturing" from "verifier not firing".
"""
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from foxio.wake_listener import WakeListener

DURATION_S = 90.0


def main():
    state = {"peak": 0.0, "rms_sum": 0.0, "n": 0}

    wl = WakeListener(on_wake=lambda: print(f"  *** WAKE at t={time.monotonic() - t0:.1f}s ***", flush=True))
    if not wl._load_detector():
        print("FAIL: no detector loaded")
        return

    orig_score = wl._score_with_verifier

    def wrapped(chunk):
        s = orig_score(chunk)
        state["peak"] = max(state["peak"], s)
        rms = float(np.sqrt(np.mean(chunk.astype(np.float64) ** 2)))
        state["rms_sum"] += rms
        state["n"] += 1
        return s

    wl._score_with_verifier = wrapped
    wl.start()
    print(f"threshold={wl.wake_threshold:.3f}\n", flush=True)

    t0 = time.monotonic()
    last_report = -1
    while time.monotonic() - t0 < DURATION_S:
        t = time.monotonic() - t0
        sec = int(t)
        if sec != last_report:
            last_report = sec
            rms = state["rms_sum"] / max(state["n"], 1)
            bar = "#" * min(int(rms / 400), 50)
            print(f"[{sec:2d}s] RMS={rms:7.1f} peak-score={state['peak']:.3f} {bar}", flush=True)
        time.sleep(0.2)

    wl.stop()
    print(f"\nDone. Peak verifier score while listening: {state['peak']:.3f} "
          f"(threshold {wl.wake_threshold:.3f}).")
    if state["n"] == 0:
        print("No audio chunks processed -> microphone not capturing input.")
    elif state["peak"] <= wl.wake_threshold:
        print("Mic captured audio but the verifier never fired on it.")


if __name__ == "__main__":
    main()
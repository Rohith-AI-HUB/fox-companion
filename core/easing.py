import math

def idle_breathing(t, amplitude=0.008, period=2.5):
    return 1.0 + amplitude * math.sin(2 * math.pi * t / period)

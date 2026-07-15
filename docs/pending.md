# Pending

**Not yet done (pick one):**

1. **Brain expansion** — add a third need (e.g. hunger/social) to `brain.py`. The doc says it's designed for extension, and it'd validate that the pattern actually works.

2. **Config file** — move constants (`WALK_ACCEL`, `GRAVITY`, brain thresholds, `MOUTH_Y`, `VOICE`/`RATE`/`PITCH`) into a single `config.py` before they sprawl further.

3. **Screen awareness** — `mss` + `pygetwindow` to detect active window and feed title into brain (highest complexity).

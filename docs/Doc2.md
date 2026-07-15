# Fox Companion — Doc2 (Voice, Speech Bubble, Dialogue, Physics Jump, Fixes)

Continuation from Doc1. Covers the voice output system, speech bubble widget, dialogue bank, and all subsequent changes.

---

## New Files

### `voice.py` — Text-to-speech engine

Synthesises speech via **edge-tts** (Azure Cognitive Services, free) and plays back on Windows.

**Voice**: `en-US-AnaNeural`, rate `+10%`, pitch `+15Hz`.

**Pipeline** (runs in a daemon thread):
1. `edge_tts.Communicate().save()` → temporary `.mp3` file
2. `miniaudio.decode_file()` → decodes MP3 to raw PCM (`DecodedSoundFile`)
3. `miniaudio.wav_write_file()` → writes a proper WAV (with `RIFF` header)
4. `winsound.PlaySound(path, SND_ASYNC)` → async playback
5. Thread sleeps for `num_frames / sample_rate` seconds, then deletes temp files

**Why miniaudio**: edge-tts always outputs MP3 regardless of file extension. `winsound` only plays WAV. `miniaudio` decodes MP3 to PCM, then `wav_write_file` produces a valid WAV that `winsound` accepts. Without this step, the WAV header is missing and playback fails.

**API**:
- `speak(text, on_start=None, on_end=None)` — enqueue speech in background thread
- `stop()` — `winsound.SND_PURGE` to cut current playback
- `is_speaking()` — whether the lock is held
- `muted` property — skip `speak()` when set

### `speech_bubble.py` — Pixel-art speech bubble

A frameless, always-on-top, click-through `QWidget` that renders a retro-style speech bubble above the fox.

**Style** (pixel-art aesthetic):
- No `Antialiasing` render hint
- `Courier New 9pt` font (monospace, pixel feel)
- Sharp `drawRect` (no rounded corners)
- Triangular tail (10×8px) pointing toward the fox's mouth
- Colors: off-white fill `#F8F4EC`, dark brown border `#32230A`, text `#1E140A`
- Word-wrap up to 200px wide

**Fade animation**:
- `QPropertyAnimation` on `windowOpacity`
- Fade-in: 150ms (0.0 → 1.0)
- Fade-out: 400ms (current → 0.0), then `self.hide()`
- `_hide_timer` — single-shot `QTimer` that triggers fade-out; **cancelled** on every new `show_text()` call so rapid triggers don't stack

**Fox tracking**:
- When `track_widget` is provided, a 30ms timer calls `_reposition()` to follow the widget
- Anchor offsets (`_anchor_dx`, `_anchor_dy`) are widget-relative (e.g., `width()//2`, `MOUTH_Y`)
- Tail tip lands exactly at `(widget.x() + dx, widget.y() + dy)`
- Tracking stops automatically on fade-out

**`show_text(text, anchor_x, anchor_y, duration_ms=3000, track_widget=None)`**:
- Computes bubble dimensions from text width and wrap limit
- With `track_widget` → stores relative offsets, starts follow timer
- Without → places at absolute screen coordinates
- Fades in, schedules auto-fade after `duration_ms`

### `dialogue.py` — Trigger-based line bank

Maps event triggers to lists of possible spoken lines:

| Trigger | Lines |
|---|---|
| `wake` | "Hey there!", "Morning!", "Oh, hi!" |
| `sit_tired` | "Phew, tired...", "Just a lil break.", "Resting my paws." |
| `jump_excited` | "Wheee!", "Boing!", "Yay!" |
| `walk_start` | "Off I go!", "Exploring time!", "Let's go!" |
| `poke_reaction` | "Hey!", "Ouch!", "Watch it!", "Stop that!" |
| `idle_bored` | "...", "So bored.", "Anything happening?" |
| `bored_walk` | "Wanna walk!", "I'm bored!", "Let's wander!" |
| `hit_ouch` | "Ow!", "That hurts!", "Rude!" |

`get_line(trigger)` returns a random line from the pool, or `""` if the trigger is unknown.

---

## Changes Since Doc1

### Physics: `jump(vy, vx=None)` method

Added to `PhysicsState` (`physics.py`):
- Sets `self.vy = vy` (negative = upward)
- If `vx` is provided, also sets horizontal velocity
- Sets `self.on_ground = False`, clears walking target
- The `step(dt)` loop already handles gravity, ceiling clamp at `anchor_y - 300`, landing detection, bounce, and squash

### Bubble anchor: `MOUTH_Y = 43`

Defined in `window.py`. The fox sprite is 64×64 centered in a 74×74 window, with visible pixels spanning y=33–63 of the sprite (y=38–68 of the window). `MOUTH_Y = 43` is the estimated mouth position within the window. All `show_text` calls use this as the vertical anchor.

### Double-click jump & escape

`window.py:mouseDoubleClickEvent` now:
1. Plays random "hit" or "jump" animation
2. Calls `physics.jump(-500, dir * 200)` — 20% chance left, 50% right
3. Fox flies upward, flings sideways, lands with bounce squash

Brain-triggered jump (`behavior.py:choose_state`) also calls `physics.jump(-400, 0)` for consistency.

### TTS MP3 decoder

`voice.py` originally saved edge-tts output as `.wav`, but edge-tts always sends MP3 audio (starts with `0xFF` sync word, not `RIFF`). Added `miniaudio.decode_file()` → `miniaudio.wav_write_file()` pipeline to produce valid WAV files for `winsound.PlaySound`.

### Speech bubble quick-disappear fix

When double-clicking rapidly, each `show_text()` call started a new `QTimer.singleShot` for fade-out, causing multiple concurrent fade-outs that overlapped incorrectly. Fixed by:
- Replacing `QTimer.singleShot()` with a stored `_hide_timer` (single-shot, reusable)
- Calling `_hide_timer.stop()` at the top of `show_text()`
- Using `self.windowOpacity()` as the fade-out start value (instead of hardcoded 1.0) to prevent opacity jumps

### Brain expansion: `hunger` need

Added a third need to `Brain` (`brain.py`):

| Need | Starts at | Passive rate | Walk/Jump rate | Sit rate | Triggers at |
|---|---|---|---|---|---|
| `energy` | 80 | +3/s idle | -5/s walk, -10/s jump | +6/s | < 25 → sit |
| `boredom` | 0 | +4/s idle | -6/s walk | +2/s sit | > 70 → walk |
| `hunger` | 10 | +1.5/s idle, sit | +4.0/s walk, jump | -4.5/s sit | > 70 → 40% sit |

`update(dt, state)` now adjusts `hunger` per state. `decide()` checks hunger after boredom but before jump: if hunger > 70 and random 40%, returns `"sit"` (the fox sits to eat).

New dialogue triggers added in `dialogue.py`:
- `"hungry"` — "Got a snack?", "I'm hungry...", "Food? Food?", "My tummy's rumbling."
- `"eating"` — "Mmm, snack time.", "Nom nom.", "Tasty!", "Yum!"

In `behavior.py:choose_state()`:
- When the brain returns `"sit"` and `hunger > 50`, speaks from `"eating"` pool instead of `"sit_tired"`
- On idle ticks, if `hunger > 60`, has a chance to speak from `"hungry"` pool

### Bubble tracking removed

The `track_widget` parameter and 30ms follow timer were removed from `SpeechBubble`. The bubble is now positioned once at a fixed screen coordinate when `show_text()` is called, and stays still while the fox walks away. Callers pass absolute screen coordinates again (`win.x() + win.width() // 2`, `win.y() + MOUTH_Y`).

---

## Architecture (updated)

```
main.py
├── PhysicsState          (physics.py)
├── RoadStrip             (road_strip.py)
├── CompanionWindow       (window.py)
│   ├── MOUTH_Y = 43
│   └── mouseDoubleClickEvent → physics.jump()
├── SpriteManager         (sprite_manager.py)
├── Behavior              (behavior.py → brain.py, dialogue.py)
├── VoiceEngine           (voice.py → edge-tts → miniaudio → winsound)
├── SpeechBubble          (speech_bubble.py)
└── QTimer 16ms render loop
    ├── physics.step(dt)
    ├── sprites.advance(dt)
    ├── win.move() / win.update()
    └── transform compositing
```

- **Voice** runs in a daemon thread; speech is decoded from MP3 to WAV via miniaudio before `winsound` playback
- **Speech bubble** renders in pixel-art style, fades in/out, positioned at a fixed screen coordinate (no tracking)
- **Dialogue** lines are chosen randomly per trigger; speech has 35% probability and 8s cooldown
- **Double-click** now applies a physics impulse (upward + random horizontal) so the fox physically jumps and escapes

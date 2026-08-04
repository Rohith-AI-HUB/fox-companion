# Fox Companion — Project Documentation

A desktop pet fox that lives on your taskbar. Built with **Python 3 + PyQt6**.

This document is the consolidated reference for the whole project (formerly Doc1–Doc3). For the wake-word system ("Hey Fox" / "Fox"), see `wake-word-fix.md`.

---

## Architecture (current)

```
main.py
├── config.py                 (constants + settings persistence)
├── PhysicsState              (physics.py → config)
├── RoadStrip                 (road_strip.py → config)
├── CompanionWindow           (window.py → config)
│   └── mouseDoubleClickEvent → physics.jump(config.DOUBLE_CLICK_*)
├── SpriteManager             (sprite_manager.py)
├── Behavior                  (behavior.py → brain.py, dialogue.py, config)
│   ├── choose_state() 4s tick → brain.update() + brain.decide()
│   ├── _speak_activity()    — no random gate, 4s cooldown
│   ├── _maybe_speak()       — 35% chance, 8s cooldown
│   └── ScreenWatcher.poll() — feeds brain.activity_category / idle_seconds
├── ScreenWatcher             (screen_watcher.py → config)
│   ├── pygetwindow           — active window title
│   └── GetLastInputInfo      — idle seconds
├── VoiceEngine               (voice.py → config → edge-tts → miniaudio → winsound)
├── SpeechBubble              (speech_bubble.py → config)
└── QTimer 16ms render loop
    ├── physics.step(dt)
    ├── sprites.advance(dt)
    ├── win.move() / win.update()
    └── transform compositing
```

- **Render** ticks at 60fps, decoupled from **state decisions** (every 4s)
- **Config** is the single source of truth for all tunable numbers — physics, brain rates, voice, bubble, cooldowns
- **Settings** persist mute and click-through state across launches via `settings.json`
- **Screen watcher** is a sensory nerve: polls active window + idle time → feeds into brain → modulates boredom/energy
- **Activity speech** has its own 4s cooldown and no probability gate, so app switches always produce a reaction
- **Indian meal schedule** drives hunger rate: breakfast, lunch, snacks, dinner windows

---

## Modules

### `main.py` — Entry point
Wires everything together:
- Creates `QApplication`, detects primary screen geometry and available (taskbar-aware) geometry
- Calculates `anchor_y` — the y-position where the fox sits on top of the taskbar
- Initializes `PhysicsState`, `RoadStrip`, `CompanionWindow`, `SpriteManager`, and `Behavior`
- Runs a **PreciseTimer at 16ms (60fps)** that drives the render loop:
  - Syncs physics with drag state
  - Calls `physics.step(dt)` for movement/gravity
  - Applies breathing scale and squash transforms
  - Determines sprite direction from velocity
  - Calls `sprites.advance(dt)` for frame animation
  - Moves the window to the physics position
  - Calls `win.update()` to trigger repaint
- Creates a **system tray icon** (fox sprite) with right-click menu:
  - Show/Hide toggle
  - Click-Through toggle
  - Quit
- Restores persisted settings on startup (`muted`, `click_through`)

### `config.py` — Central constants & settings persistence

Replaces hardcoded constants scattered across 6 modules. All tuning values live here:

| Section | Constants |
|---|---|
| Physics | `WALK_ACCEL`, `WALK_MAX_SPEED`, `WALK_DECEL_RADIUS`, `FRICTION`, `GRAVITY`, `BOUNCE_VELOCITY`, `POSITION_THRESHOLD`, `CEILING_OFFSET` |
| Window | `WINDOW_SIZE` (74), `MOUTH_Y` (43) |
| Walk speed | `MIN_WALK_SPEED` (80), `MAX_WALK_SPEED` (300) |
| Brain needs | `ENERGY_*_RATE`, `BOREDOM_*_RATE`, `HUNGER_*_RATE`, all thresholds (`ENERGY_SLEEP_THRESHOLD`, `BOREDOM_WALK_THRESHOLD`, etc.) |
| Meal times | `MEAL_HOURS` — Indian schedule (see below) |
| Night hours | `NIGHT_START_HOUR` (22), `NIGHT_END_HOUR` (6) |
| Screen watcher | `IDLE_AFK_SECONDS` (120), `IDLE_NAP_SECONDS` (300) |
| Voice | `VOICE_NAME`, `VOICE_RATE`, `VOICE_PITCH` |
| Speech bubble | `BUBBLE_*` (padding, wrap, font, fade, colors) |
| Cooldowns | `SPEAK_COOLDOWN` (8s), `ACTIVITY_SPEAK_COOLDOWN` (4s), `SPEAK_CHANCE` (0.35) |
| Override | `OVERRIDE_DURATION_MS` (15000) |
| Jump physics | `DOUBLE_CLICK_JUMP_VY/VX`, `BRAIN_JUMP_VY` |
| Wake word | `WAKE_THRESHOLD_DEFAULT` (0.5) |

`meal_factor()` returns 1.5 during meal hours, 0.5 otherwise — used by `Brain.update()` to modulate hunger rate.

`load_settings()` / `save_settings(data)` read/write `settings.json` for persistence.

### `window.py` — CompanionWindow (the fox widget)
Frameless, always-on-top, translucent widget (`74×74px`).
- **`set_frame(path, direction)`** — loads a pixmap and triggers repaint
- **`paintEvent`** — draws the current frame via `QPainter`, applying:
  - Horizontal flip for direction
  - `_transform_scale` for idle breathing oscillation
  - `_transform_squash` for landing bounce
- **Mouse drag** — tracks position history (last 4 samples) with timestamps; on release computes release velocity (px/s) and passes it to physics for flick-to-slide momentum
- **Double-click** — random "hit" or "jump", plays once then returns to idle; also applies a physics impulse so the fox physically jumps and escapes
- **Right-click context menu** — Idle / Walk / Sit / Jump / Hit
- **`on_action(action)`** — dispatches user commands, stops continuous walk for non-walk actions
- **`toggle_click_through()`** — toggles `WA_TransparentForMouseEvents`

`MOUTH_Y = 43` is the estimated mouth position within the window (sprite 64×64 centered in 74×74). All bubble `show_text` calls use this as the vertical anchor.

### `sprite_manager.py` — Frame animation manager
Owns no timer — driven by the render loop via `advance(dt)`.
- **`play(action, fps, loop)`** — discovers frames by counting `{action}_N.png` files in `assets/fox/`, stores sorted path list
- **`advance(dt)`** — accumulates delta-time, advances to the next frame when the per-frame interval elapses (independent of render rate). Calls `window.set_frame()` each step. Fires `on_finish` callback when a non-looping sequence completes.
- **`_count(action)`** — safely counts files matching `{action}_\d+.png` (ignores prefix collisions like `sit_idle` when action is `sit`)
- **`current_frame_path()`** — returns the path of the current frame

### `behavior.py` — State machine & decision scheduling
- Runs a `QTimer` every **4 seconds** calling `choose_state()`
- **`set_continuous_walk(enabled)`** — manual walk mode (from context menu), walks to right edge. While active, `choose_state()` is suppressed. When disabled, sets an 8-second manual override to prevent the brain from immediately overriding the user's choice.
- **`_manual_override`** — flag that suppresses brain decisions for 8s after any manual action (Idle/Sit/Jump/Hit from context menu). Cleared by `_override_timer`.
- **`choose_state()`** — updates the `Brain`, calls `brain.decide()`, then dispatches:
  - `walk` → sets physics target to left or right edge
  - `sit` → plays sit (no loop) → chains to `sit_idle`
  - `jump` → plays jump (no loop) → chains to `idle`
  - default → plays `idle`
- **`_screen_bounds()`** — uses `QApplication.screenAt()` to find the current screen's geometry (multi-monitor aware)

`choose_state()` also:
1. Calls `watcher.poll()` every tick (4s)
2. Syncs `brain.activity_category` and `brain.user_idle_seconds`
3. Detects transitions and speaks activity-appropriate lines (see Screen awareness)

**`_speak_activity(trigger)`** — dedicated method for activity-triggered speech:
- Separate cooldown (`ACTIVITY_SPEAK_COOLDOWN = 4s`, matches brain tick)
- No probability gate (fires reliably on every transition)
- Uses separate `_last_activity_speak` timer

### `physics.py` — Physics engine
Constants at module level (moved to `config.py`):
- `WALK_ACCEL = 800.0` — acceleration toward max walk speed (px/s²)
- `WALK_MAX_SPEED = 200.0` — max walking speed
- `WALK_DECEL_RADIUS = 60.0` — distance from target where deceleration begins (ease-out)
- `FRICTION = 6.0` — friction coefficient for drag-release momentum decay
- `GRAVITY = 900.0` — gravity acceleration (px/s²)
- `BOUNCE_VELOCITY = 60.0` — velocity threshold below which landing stops bouncing
- `POSITION_THRESHOLD = 2.0` — distance threshold to consider target reached

**`PhysicsState`** tracks `x, y, vx, vy, on_ground, target_x, walking, landing_bounce, squash`.
- **`walk_to(target_x)`** / **`stop_walk()`** — set/clear walking target
- **`jump(vy, vx=None)`** — sets upward velocity (and optional horizontal), clears ground + walking target
- **`release_from_drag(x, y, vx, vy)`** — called on mouse release; sets position + velocity, determines `on_ground` based on anchor_y
- **`step(dt)`** — semi-implicit Euler:
  - Walking → accelerates toward `WALK_MAX_SPEED` via `WALK_ACCEL`; decelerates smoothly within `WALK_DECEL_RADIUS` of target (ease-out curve, not instant stop)
  - Idle → friction decelerates `vx`, zero-out below 2px/s
  - Boundary clamp → at edges, snaps to bound; if walking, auto-reverses `target_x`
  - Off-ground → gravity, ceiling clamp at `anchor_y - 300`, landing detection with bounce and squash
  - Landing bounce → `squash` interpolates from 0.85 → 1.0

### `easing.py` — Easing function library
- **`clamp(v, lo, hi)`** — value clamping
- **`ease_out_cubic(t)`** — smooth deceleration
- **`ease_in_out_quad(t)`** — smooth acceleration/deceleration
- **`ease_out_elastic(t)`** — elastic overshoot
- **`spring_damper(x, v, target, stiffness, damping, dt)`** — implicit Euler critically-damped spring
- **`idle_breathing(t, amplitude, period)`** — sine-wave oscillation at ~1 ±0.8%, 2.5s period

### `brain.py` — Needs-based AI (decision system)
Tracks three needs (0–100):

| Need | Starts at | Passive rate | Walk/Jump rate | Sit rate | Triggers at |
|---|---|---|---|---|---|
| `energy` | 80 | +3/s idle | -5/s walk, -10/s jump | +6/s | < 25 → sit |
| `boredom` | 0 | +4/s idle | -6/s walk | +2/s sit | > 70 → walk |
| `hunger` | 10 | +1.5/s idle, sit | +4.0/s walk, jump | -4.5/s sit | > 70 → 40% sit |

**`update(dt, state)`** — adjusts needs based on current state × elapsed time. Also:
- If `activity_category == "coding"` and not idle → boredom rate × `CODING_FOCUS_MULTIPLIER` (0.5)
- If `user_idle_seconds >= IDLE_AFK` → energy recovers at `NAP_FOCUS_RATE` (2.0/s) instead of per-state rates

**`decide()`** checks conditions in strict order. First match wins:

| Priority | Condition | Action | Trigger |
|---|---|---|---|
| 1 | `idle_seconds ≥ 300` (user AFK >5min) | sit | `user_nap` |
| 2 | Night hour (22–06) AND (`energy < 60` OR 60% random) | sit | `late` |
| 3 | `energy < 25` | sit | `sit_tired` |
| 4 | `boredom > 70` | walk | `walk_start` |
| 5 | `hunger > 70` AND 40% random | sit | `eating` / `sit_tired` |
| 6 | `boredom > 50` AND `energy > 50` AND 50% random | jump | `jump_excited` |
| 7 | `energy > 80` AND `boredom > 30` AND 40% random | walk (slow) | `walk_start` |
| 8 | (fallback) | idle | `idle_bored` / `hungry` |

Example overlap trace:
- **11pm, boredom=80, energy=50, idle=0s** → Priority 2 fires (night + energy<60) → returns sit. Boredom never evaluated.
- **2pm, boredom=40, hunger=75, energy=60** → Priorities 1-3 pass, 4 fails (boredom ≤70), Priority 5 fires (hunger>70 + 40%) → returns sit.

Designed for extension — add more needs (social, curiosity) and they'll naturally influence decisions.

### `road_strip.py` — Decorative road strip
Separate always-on-top, click-through widget positioned at the taskbar top edge, behind the fox.
- **8px tall**, full screen width
- Tiles a 32×8px PNG (`assets/road_tile.png`) via `QPainter.drawTiledPixmap` (no stretching, seamless on any width)
- `lower()` is called after creation so the fox renders in front

### `screen_watcher.py` — Active window & idle detection

Polling-based sensory input module. Runs on a 4s timer (inside `Behavior.choose_state()`).

**Windows API**:
- `GetLastInputInfo` (ctypes) — time since last keyboard/mouse input
- `GetTickCount` — current uptime tick for idle calculation
- `pygetwindow.getActiveWindow()` — active window title

**State**:
| Field | Type | Description |
|---|---|---|
| `active_title` | str | Current foreground window title |
| `category` | str | `coding`, `browsing`, `communication`, `gaming`, `other` |
| `idle_seconds` | float | Seconds since last user input |
| `transition` | str or None | `became_active`, `became_idle`, `category_changed`, `None` |

**Category detection** — substring match on window title:
- `coding`: "visual studio", "code", "pycharm", "trae", "terminal", etc.
- `browsing`: "chrome", "firefox", "zen", etc.
- `communication`: "discord", "slack", "teams", etc.
- `gaming`: "game", "minecraft", "steam"

**Idle thresholds**:
- `IDLE_AFK_SECONDS` (120) — user considered away
- `IDLE_NAP_SECONDS` (300) — brain returns "sit" and fox naps

**Transition detection** — pure idle-state tracking (not window-title dependent):
- `_was_idle` flag from previous poll
- Transition set on change, not on title match (fixes bug where switching apps masked idle transitions)

### `screen_reader.py` — Watches the user's display

Every `SCREEN_READ_INTERVAL_S` (5 s) the fox captures the primary screen via
Qt (`QScreen.grabWindow`) on the GUI thread, runs **coarse change-detection**
(a tiny downscaled grayscale grid, so the cursor and micro-flash are ignored),
and only when the screen meaningfully changes sends the frame to a vision
model for a one-sentence summary of the user's activity.

- **Providers:** Groq vision (`llama-3.2-11b-vision-preview`) is primary;
  OpenAI vision (`gpt-4o-mini`, the models behind ChatGPT) is the fallback if
  `OPENAI_API_KEY` is set. If both are unavailable, nothing is learned.
- **Memory:** a summary that is meaningfully different from the last is stored
  via `FoxBrain.capture_async` (previous observation is superseded), so the
  fox builds a picture of the user's daily activity.
- **Privacy:** screenshots exist only in memory — **never written to disk**.
  All vision calls run on a single background worker so the UI stays responsive.

### `brain/brain.py` — Long-term memory (FoxBrain)

The vault at `brain/vault/` is the fox's persistent memory store:

| Item | Role |
|---|---|
| `raw/*.md` | Markdown mirror of every captured fact |
| `keyword_search.db` | SQLite: `facts` table (with temporal validity + embedding BLOB) and FTS5 index for exact-match search |

- **Capture** — facts are embedded with FastEmbed (`bge-small-en-v1.5`); the
  384-dim vector is stored **as a BLOB in the `facts` table** (no separate
  vector DB). Entity-key extraction and contradiction detection use Groq.
- **Recall** — hybrid: semantic (numpy cosine over stored embeddings) merged
  with exact (SQLite FTS5), both respecting temporal validity (superseded
  facts are excluded unless a historical query is asked).
- **Why no ChromaDB** — for a personal memory of hundreds of facts,
  brute-force numpy cosine over a few thousand floats is instant, so the
  heavyweight vector DB was removed and its on-disk `chroma_db/` folder
  deleted. Existing facts are backfilled automatically on first run.

### `voice.py` — Text-to-speech engine

Synthesises speech via **edge-tts** (Azure Cognitive Services, free) and plays back on Windows.

**Voice**: configured in `config.py` (`VOICE_NAME` etc.).

**Pipeline** (runs in a daemon thread):
1. `edge_tts.Communicate().save()` → temporary `.mp3` file
2. `miniaudio.decode_file()` → decodes MP3 to raw PCM (`DecodedSoundFile`)
3. `miniaudio.wav_write_file()` → writes a proper WAV (with `RIFF` header)
4. `winsound.PlaySound(path, SND_ASYNC)` → async playback
5. Thread sleeps for `num_frames / sample_rate` seconds, then deletes temp files

**Why miniaudio**: edge-tts always outputs MP3 regardless of file extension. `winsound` only plays WAV. `miniaudio` decodes MP3 to PCM, then `wav_write_file` produces a valid WAV that `winsound` accepts. Without this step, the WAV header is missing and playback fails.

**API**:
- `speak(text, on_start=None, on_end=None)` — enqueue speech in background thread (uses a `threading.Lock`, so back-to-back calls queue sequentially)
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
- `_hide_timer` — single-shot `QTimer` that triggers fade-out; **cancelled** on every new `show_text()` call so rapid triggers don't stack (uses `windowOpacity()` as the fade-out start to prevent opacity jumps)

**`show_text(text, anchor_x, anchor_y, duration_ms=3000, track_widget=None)`**:
- Computes bubble dimensions from text width and wrap limit
- Positioned once at a fixed screen coordinate; callers pass absolute screen coordinates (`win.x() + win.width() // 2`, `win.y() + MOUTH_Y`)
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
| `hungry` | "Got a snack?", "I'm hungry...", "Food?", "Food?", "My tummy's rumbling." |
| `eating` | "Mmm, snack time.", "Nom nom.", "Tasty!", "Yum!" |
| `mealtime` | "Is it snack time?", "Smells good!", "Food time?", "Tummy's ready!", "Breakfast time!", etc. |

Plus (added since Doc2): `late`, `manual_sit/walk/jump/hit/idle`, `reluctant_walk/sit/idle`, `user_coding`, `user_browsing`, `user_chatting`, `user_afk`, `user_back`, `user_focused`, `user_nap`, `user_wake`, and time-of-day wake greetings (morning/afternoon/evening).

`get_line(trigger)` returns a random line from the pool, or `""` if the trigger is unknown.

### `requirements.txt` — Dependency manifest

```
PyQt6>=6.5
edge-tts>=6.0
miniaudio>=1.70
pygetwindow>=0.0.9
Pillow>=9.0,<12.0
groq>=0.11.0
python-dotenv>=1.0
keyboard>=0.13
openwakeword>=0.6
onnxruntime>=1.16
sounddevice>=0.4
numpy>=1.24
scipy>=1.11
fastembed>=0.2.0
SpeechRecognition>=3.10
# openai>=1.0   # optional: ChatGPT-family fallback for screen vision
```

---

## Behavior notes

### Audio overlap prevention

`_speak_activity` and `_maybe_speak` have separate cooldown pools, so both could fire in the same 4s tick. Fixed by:
- `_activity_spoke` boolean flag, reset at the start of each `choose_state()` tick
- `_speak_activity` sets `_activity_spoke = True` when it fires
- `_maybe_speak` returns early if `_activity_spoke` is True
- Result: only one speech per tick — activity lines take priority over state-trigger lines

`VoiceEngine.speak()` uses a `threading.Lock` in `_run()`, so back-to-back calls queue sequentially (second waits for first to finish synthesis + playback). No dropped audio, no overlap.

### Mealtime awareness

In `Behavior.choose_state()`, if `config.meal_factor() > 1.0` (current time is within a meal window), the fox has a 35% chance per 4s tick to speak a mealtime line. This is independent of accumulated hunger — purely clock-based.

`MEAL_HOURS` in `config.py`:
| Meal | Time | Factor |
|---|---|---|
| Breakfast | 7:30 – 8:30 | 1.5 |
| Lunch | 12:30 – 13:30 | 1.5 |
| Snacks | 16:00 – 16:40 | 1.5 |
| Dinner | 19:30 – 20:30 | 1.5 |
| Off-peak | — | 0.5 |

### Manual action responses (cute reactions)

Right-click context menu actions now speak + bubble:
- `manual_sit` — "Okay, resting!", "Sure, I'll sit."
- `manual_walk` — "Let's go!", "Walking time!"
- `manual_jump` — "Wheee!", "Boing!"
- `manual_hit` — "Ouch! Why?", "Hey, that hurt!"
- `manual_idle` — "Just hanging out.", "Alright, chill mode."

Manual override duration increased to 15s (up from 8s).

### Reluctant transitions

When the brain overrides a manual action after 15s, the fox speaks 50% of the time:
- `reluctant_walk` — "Fine, I'll walk...", "If you insist..."
- `reluctant_sit` — "I guess I'll sit...", "Fine, resting."
- `reluctant_idle` — "Fine, I'll stay.", "Okay, okay.", "Hmph."

### Night behavior

`Brain.decide()` checks hour: if ≥ 22 or < 6, returns `"sit"` 60%+ of the time (sleepy mode). `_is_late()` used by `Behavior` to speak `"late"` lines ("Zzz... getting sleepy.").

### Settings persistence

`config.load_settings()` / `config.save_settings()` read/write `settings.json` in the project directory.

Persisted on every toggle:
- `muted` — voice mute state
- `click_through` — click-through toggle state

Saved on Quit via `do_quit()`. Restored on startup in `main.py`:
- `voice.muted = settings.get("muted", False)`
- `if settings.get("click_through", False): win.toggle_click_through()`

### Transition → trigger mapping (screen awareness)

| Transition | Trigger | Example line |
|---|---|---|
| `became_active` | `user_back` | "You're back!" |
| `became_idle` | `user_afk` | "Where'd you go?" |
| `category_changed` (coding) | `user_coding` | "Ooh, code!" |
| `category_changed` (browsing) | `user_browsing` | "Scrolling again?" |
| `category_changed` (communication) | `user_chatting` | "Who ya talking to?" |
| `category_changed` (other) | `user_focused` | "Focus mode!" |
| is_napping | `user_nap` | "Zzz..." |
| wake from nap | `user_wake` | "Rise and shine!" |

### Notable fixes

- **`_last_speak` init fix** — `self._last_speak` was `0.0`, so the first `_maybe_speak` at t=4s was blocked (4.0 − 0.0 < 8.0). Changed to `time.time() - config.SPEAK_COOLDOWN` so cooldown is immediately expired at startup.
- **Transition detection fix** — original `_was_idle` check required `prev_title == active_title`, which meant switching apps (title change) masked `became_active`. Fixed by tracking `_was_idle` as a separate boolean flag, independent of window title.
- **Bubble quick-disappear fix** — rapid double-clicks stacked fade-outs; replaced `QTimer.singleShot()` with a stored reusable `_hide_timer` stopped at the top of `show_text()`.
- **TTS MP3 decoder** — added miniaudio decode → WAV rewrite pipeline (edge-tts sends MP3 even when asked for WAV).

---

## Assets

- `assets/fox/` — frame sprites (`idle_N.png`, `sit_N.png`, `sit_idle_N.png`, `walk_N.png`, `jump_N.png`, `hit_N.png`)
- `assets/road_tile.png` — 32×8px tileable road strip (see generator below)
- `assets/wake/` — wake-word verifier `fox_verifier.pkl` — see `wake-word-fix.md`
- `assets/enroll/` — user voice enrollment clips for verifier training

### `generate_road_tile.py` — Road tile asset generator

Run once (`python tools\generate_road_tile.py`) to produce `assets/road_tile.png`:
- **32×8px** RGBA
- 2px green grass fringe (dithered)
- 6px dirt brown body (4 alternating shades, pixel noise, random dark flecks)
- Light highlight on the top dirt edge

Replace with hand-drawn art later by overwriting `assets/road_tile.png` with a same-size or tileable PNG.

---

## Pending items (status)

The original task tracker listed three priorities — all now **done**:
1. ~~Brain expansion (hunger need)~~ — done
2. ~~Config file consolidation~~ — done
3. ~~Screen awareness~~ — done

# Fox Companion — Doc3 (Config, Screen Awareness, Settings, Indian Meals)

Continuation from Doc2. Covers config consolidation, screen watcher, settings persistence, Indian meal schedule, manual action responses, and all fixes since Doc2.

---

## New Files

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

`meal_factor()` returns 1.5 during meal hours, 0.5 otherwise — used by `Brain.update()` to modulate hunger rate.

`load_settings()` / `save_settings(data)` read/write `settings.json` for persistence.

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

### `requirements.txt` — Dependency manifest

```
PyQt6>=6.5
edge-tts>=6.0
miniaudio>=1.70
pygetwindow>=0.0.9
Pillow>=9.0
```

---

## Brain decision priority

`decide()` checks conditions in strict order. First match wins:

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

## Audio overlap prevention

`_speak_activity` and `_maybe_speak` have separate cooldown pools, so both could fire in the same 4s tick. Fixed by:
- `_activity_spoke` boolean flag, reset at the start of each `choose_state()` tick
- `_speak_activity` sets `_activity_spoke = True` when it fires
- `_maybe_speak` returns early if `_activity_spoke` is True
- Result: only one speech per tick — activity lines take priority over state-trigger lines

`VoiceEngine.speak()` uses a `threading.Lock` in `_run()`, so back-to-back calls queue sequentially (second waits for first to finish synthesis + playback). No dropped audio, no overlap.

## Mealtime awareness

`mealtime` dialogue trigger: "Is it snack time?", "Smells good!", "Food time?", "Tummy's ready!", "Breakfast time!", etc.

In `Behavior.choose_state()`, if `config.meal_factor() > 1.0` (current time is within a meal window), the fox has a 35% chance per 4s tick to speak a mealtime line. This is independent of accumulated hunger — purely clock-based.

## Changes Since Doc2

### Config consolidation

All module-level constants moved to `config.py`. Each file now imports `import config` and references `config.XXX`:
- `physics.py` — removed 7 module-level vars
- `brain.py` — removed `_meal_factor()`, `HUNGER_*` rates; uses `config.meal_factor()` and `config.*`
- `voice.py` — removed `VOICE`, `RATE`, `PITCH`; uses `config.VOICE_NAME`, `config.VOICE_RATE`, `config.VOICE_PITCH`
- `behavior.py` — removed `MIN_WALK_SPEED`, `MAX_WALK_SPEED`; uses `config.*`
- `window.py` — removed `SIZE`, `BASE_SIZE`, `MOUTH_Y`; uses `config.WINDOW_SIZE`, `config.MOUTH_Y`
- `main.py` — removed `ROAD_HEIGHT` import; uses `config.ROAD_HEIGHT`
- `road_strip.py` — removed `ROAD_HEIGHT`; imports `config.ROAD_HEIGHT`
- `screen_watcher.py` — removed `IDLE_AFK`, `IDLE_NAP`; uses `config.IDLE_AFK_SECONDS`, `config.IDLE_NAP_SECONDS`

### Indian meal schedule

`MEAL_HOURS` in `config.py`:
| Meal | Time | Factor |
|---|---|---|
| Breakfast | 7:30 – 8:30 | 1.5 |
| Lunch | 12:30 – 13:30 | 1.5 |
| Snacks | 16:00 – 16:40 | 1.5 |
| Dinner | 19:30 – 20:30 | 1.5 |
| Off-peak | — | 0.5 |

`config.meal_factor()` checks `datetime.now()` against each window. Used by `Brain.update()` to make hunger rise faster near meal times.

### Settings persistence

`config.load_settings()` / `config.save_settings()` read/write `settings.json` in the project directory.

Persisted on every toggle:
- `muted` — voice mute state
- `click_through` — click-through toggle state

Saved on Quit via `do_quit()`. Restored on startup in `main.py`:
- `voice.muted = settings.get("muted", False)`
- `if settings.get("click_through", False): win.toggle_click_through()`

### Screen awareness wiring

`Behavior.choose_state()` now:
1. Calls `watcher.poll()` every tick (4s)
2. Syncs `brain.activity_category` and `brain.user_idle_seconds`
3. Detects transitions and speaks activity-appropriate lines

**`_speak_activity(trigger)`** — dedicated method for activity-triggered speech:
- Separate cooldown (`ACTIVITY_SPEAK_COOLDOWN = 4s`, matches brain tick)
- No probability gate (fires reliably on every transition)
- Uses separate `_last_activity_speak` timer

**Transition → trigger mapping**:

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

### Brain: activity-aware needs

`Brain.update()` now:
- If `activity_category == "coding"` and not idle → boredom rate × `CODING_FOCUS_MULTIPLIER` (0.5)
- If `user_idle_seconds >= IDLE_AFK` → energy recovers at `NAP_FOCUS_RATE` (2.0/s) instead of per-state rates
- `decide()` returns `"sit"` if `user_idle_seconds >= IDLE_NAP` (300s)

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

### `_last_speak` init fix

`self._last_speak` was `0.0`, so the first `_maybe_speak` at t=4s was blocked (4.0 − 0.0 < 8.0). Changed to `time.time() - config.SPEAK_COOLDOWN` so cooldown is immediately expired at startup.

### Transition detection fix (screen_watcher)

Original `_was_idle` check required `prev_title == active_title`, which meant switching apps (title change) masked `became_active`. Fixed by tracking `_was_idle` as a separate boolean flag, independent of window title.

### Night behavior

`Brain.decide()` checks hour: if ≥ 22 or < 6, returns `"sit"` 60%+ of the time (sleepy mode). `_is_late()` used by `Behavior` to speak `"late"` lines ("Zzz... getting sleepy.").

### Dialogue trigger additions

Since Doc2:
- `hungry`, `eating`, `late`, `manual_sit/walk/jump/hit/idle`, `reluctant_walk/sit/idle`
- `user_coding`, `user_browsing`, `user_chatting`, `user_afk`, `user_back`, `user_focused`, `user_nap`, `user_wake`
- Time-of-day wake greetings (morning/afternoon/evening)

### `pending.md`

Created as a task tracker listing the three remaining priorities:
1. Brain expansion (done)
2. Config file (done)
3. Screen awareness (done)

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

- **Config** is the single source of truth for all tunable numbers — physics, brain rates, voice, bubble, cooldowns
- **Settings** persist mute and click-through state across launches via `settings.json`
- **Screen watcher** is a sensory nerve: polls active window + idle time → feeds into brain → modulates boredom/energy
- **Activity speech** has its own 4s cooldown and no probability gate, so app switches always produce a reaction
- **Indian meal schedule** drives hunger rate: breakfast, lunch, snacks, dinner windows

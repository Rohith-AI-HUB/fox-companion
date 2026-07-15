# Fox Companion — Project Overview

A desktop pet fox that lives on your taskbar. Built with **Python 3 + PyQt6**.

---

## Files

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

### `window.py` — CompanionWindow (the fox widget)
Frameless, always-on-top, translucent widget (`74×74px`).
- **`set_frame(path, direction)`** — loads a pixmap and triggers repaint
- **`paintEvent`** — draws the current frame via `QPainter`, applying:
  - Horizontal flip for direction
  - `_transform_scale` for idle breathing oscillation
  - `_transform_squash` for landing bounce
- **Mouse drag** — tracks position history (last 4 samples) with timestamps; on release computes release velocity (px/s) and passes it to physics for flick-to-slide momentum
- **Double-click** — random "hit" or "jump", plays once then returns to idle
- **Right-click context menu** — Idle / Walk / Sit / Jump / Hit
- **`on_action(action)`** — dispatches user commands, stops continuous walk for non-walk actions
- **`toggle_click_through()`** — toggles `WA_TransparentForMouseEvents`

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

### `physics.py` — Physics engine
Constants at module level for easy tuning:
- `WALK_ACCEL = 800.0` — acceleration toward max walk speed (px/s²)
- `WALK_MAX_SPEED = 200.0` — max walking speed
- `WALK_DECEL_RADIUS = 60.0` — distance from target where deceleration begins (ease-out)
- `FRICTION = 6.0` — friction coefficient for drag-release momentum decay
- `GRAVITY = 900.0` — gravity acceleration (px/s²)
- `BOUNCE_VELOCITY = 60.0` — velocity threshold below which landing stops bouncing
- `POSITION_THRESHOLD = 2.0` — distance threshold to consider target reached

**`PhysicsState`** tracks `x, y, vx, vy, on_ground, target_x, walking, landing_bounce, squash`.
- **`walk_to(target_x)`** / **`stop_walk()`** — set/clear walking target
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

### `brain.py` — Needs-based AI (simple decision system)
Tracks two needs (0–100):
- **`energy`** — drains during walk/jump, recovers during idle/sit
- **`boredom`** — builds during idle/sit, relieves during walk/jump

**`update(dt, state)`** — adjusts needs based on current state × elapsed time. **`decide()`** returns the next action:
- Energy < 25 → `sit` (rest)
- Boredom > 70 → `walk` (explore)
- Boredom > 50 & energy > 50 → 50% chance `jump`
- Energy > 80 & boredom > 30 → 40% chance `walk`
- Otherwise → `idle` (content)

Designed for extension — add more needs (hunger, social, curiosity) and they'll naturally influence decisions.

### `road_strip.py` — Decorative road strip
Separate always-on-top, click-through widget positioned at the taskbar top edge, behind the fox.
- **8px tall**, full screen width
- Tiles a 32×8px PNG (`assets/road_tile.png`) via `QPainter.drawTiledPixmap` (no stretching, seamless on any width)
- `lower()` is called after creation so the fox renders in front

### `generate_road_tile.py` — Road tile asset generator
Run once (`python generate_road_tile.py`) to produce `assets/road_tile.png`:
- **32×8px** RGBA
- 2px green grass fringe (dithered)
- 6px dirt brown body (4 alternating shades, pixel noise, random dark flecks)
- Light highlight on the top dirt edge

Replace with hand-drawn art later by overwriting `assets/road_tile.png` with a same-size or tileable PNG.

### `test_frames.py` *(removed)*
Was a standalone visual test for frame ordering. Superseded by the full app.

---

## Architecture summary

```
main.py
├── PhysicsState          (physics.py)
├── RoadStrip             (road_strip.py)
├── CompanionWindow       (window.py)
├── SpriteManager         (sprite_manager.py)
├── Behavior              (behavior.py → brain.py)
└── QTimer 16ms render loop
    ├── physics.step(dt)
    ├── sprites.advance(dt)
    ├── win.move() / win.update()
    └── transform compositing
```

- **Render** ticks at 60fps, decoupled from **state decisions** (every 4s)
- **Walking** uses constant velocity with instant start/stop; **drag** uses friction-based momentum decay
- **Gravity/landing** uses acceleration with bounce squash
- **Idle** has subtle breathing via sine-wave scale oscillation
- **Brain** drives autonomous state selection based on energy/boredom needs

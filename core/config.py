import os
import json
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()


def now() -> datetime:
    """Return the current local datetime (single authoritative source)."""
    return datetime.now()


def hour() -> int:
    """Return the current hour (0-23). Convenience wrapper around now()."""
    return now().hour

# ── Physics ──
WALK_ACCEL = 800.0
WALK_MAX_SPEED = 200.0
WALK_DECEL_RADIUS = 60.0
FRICTION = 6.0
GRAVITY = 900.0
BOUNCE_VELOCITY = 60.0
POSITION_THRESHOLD = 2.0
CEILING_OFFSET = 300

# ── Window ──
WINDOW_SIZE = 74
MOUTH_Y = 43

# ── Sprite ──
# ── Walk speed range (behavior) ──
MIN_WALK_SPEED = 80.0
MAX_WALK_SPEED = 300.0

# ── Brain needs ──
ENERGY_INITIAL = 80.0
BOREDOM_INITIAL = 0.0
HUNGER_INITIAL_OFFPEAK = 10.0
HUNGER_INITIAL_MEAL = 30.0

ENERGY_IDLE_RATE = 3.0
ENERGY_SIT_RATE = 6.0
ENERGY_WALK_RATE = -5.0
ENERGY_JUMP_RATE = -10.0

BOREDOM_IDLE_RATE = 4.0
BOREDOM_SIT_RATE = 2.0
BOREDOM_WALK_RATE = -6.0
BOREDOM_JUMP_RATE = -5.0

HUNGER_PASSIVE_RATE = 1.5
HUNGER_ACTIVE_RATE = 4.0
HUNGER_REST_RATE = -4.5

ENERGY_SLEEP_THRESHOLD = 25
BOREDOM_WALK_THRESHOLD = 70
HUNGER_SIT_THRESHOLD = 70
BOREDOM_JUMP_THRESHOLD = 50
ENERGY_JUMP_THRESHOLD = 50
ENERGY_WALK_THRESHOLD = 80
BOREDOM_WALK_TRIGGER = 30

JUMP_PROBABILITY = 0.5
WALK_PROBABILITY = 0.4
HUNGER_SIT_PROBABILITY = 0.4

BRAIN_TICK_INTERVAL = 4.0

# ── Meal times (Indian schedule) ──
MEAL_HOURS = [
    (7, 30, 8, 30),    # breakfast
    (12, 30, 13, 30),  # lunch
    (16, 0, 16, 40),   # snacks
    (19, 30, 20, 30),  # dinner
]

def meal_factor() -> float:
    n = now()
    for sh, sm, eh, em in MEAL_HOURS:
        start = n.replace(hour=sh, minute=sm, second=0)
        end = n.replace(hour=eh, minute=em, second=0)
        if start <= n <= end:
            return 1.5
    return 0.5

# ── Night hours ──
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6

def is_night() -> bool:
    h = hour()
    return h >= NIGHT_START_HOUR or h < NIGHT_END_HOUR

# ── Activity / idle (screen watcher) ──
IDLE_AFK_SECONDS = 120.0
IDLE_NAP_SECONDS = 300.0
NAP_FOCUS_RATE = 2.0
CODING_FOCUS_MULTIPLIER = 0.5

# ── Voice ──
VOICE_NAME = "en-IN-NeerjaNeural"
VOICE_RATE = "+10%"
VOICE_PITCH = "+15Hz"

# ── Wake word ──
# Trigger score threshold for 'Hey Fox' / 'Fox' detection. Can be overridden
# per-install via the "wake_threshold" key in settings.json (higher = fewer
# false positives, lower = more sensitive).
WAKE_THRESHOLD_DEFAULT = 0.5

# ── Speech bubble ──
BUBBLE_PADDING = 14
BUBBLE_WRAP_WIDTH = 200
BUBBLE_FONT_SIZE = 9
BUBBLE_FONT_FAMILY = "Courier New"
BUBBLE_FADE_IN_MS = 150
BUBBLE_FADE_OUT_MS = 400
BUBBLE_DURATION_MS = 3000
BUBBLE_TAIL_W = 10
BUBBLE_TAIL_H = 8
BUBBLE_RADIUS = 6
BUBBLE_SHADOW_COLOR = (30, 20, 10, 60)
BUBBLE_SHADOW_OFFSET = (2, 3)
BUBBLE_SHADOW_BLUR = 4
BUBBLE_BG = (248, 244, 236, 240)
BUBBLE_BORDER = (50, 30, 10)
BUBBLE_TEXT_COLOR = (30, 20, 10)
BUBBLE_BG_HEX = "#F8F4EC"
BUBBLE_BORDER_HEX = "#321E0A"
BUBBLE_TEXT_HEX = "#1E140A"

# Night bubble variants
BUBBLE_BG_NIGHT = (40, 36, 50, 230)
BUBBLE_BORDER_NIGHT = (100, 90, 130)
BUBBLE_TEXT_COLOR_NIGHT = (220, 215, 240)
BUBBLE_BG_NIGHT_HEX = "#282432"
BUBBLE_BORDER_NIGHT_HEX = "#645A82"
BUBBLE_TEXT_NIGHT_HEX = "#DCD7F0"

# ── Thinking state ──
THINKING_DOTS_INTERVAL = 500  # ms between dot changes
THINKING_BOUNCE_AMP = 0.04
THINKING_BOUNCE_FREQ = 3.0

# ── Behavior speech cooldowns ──
SPEAK_COOLDOWN = 8.0
SPEAK_CHANCE = 0.35
ACTIVITY_SPEAK_COOLDOWN = 4.0

# ── Manual override ──
OVERRIDE_DURATION_MS = 15000

# ── Jump physics ──
DOUBLE_CLICK_JUMP_VY = -500
DOUBLE_CLICK_JUMP_VX = 200
BRAIN_JUMP_VY = -400

# ── Road strip ──
ROAD_HEIGHT = 8
ROAD_TILE_PATH = "assets/road_tile.png"

# ── Particles ──
PARTICLE_MAX_COUNT = 30
DUST_SPAWN_INTERVAL = 0.15
DUST_LIFETIME = 0.6
DUST_SIZE = 2
LANDING_PUFF_COUNT = 6
BONK_STAR_LIFETIME = 0.5
BONK_STAR_COUNT = 3
Zzz_SPAWN_INTERVAL = 3.0
Zzz_LIFETIME = 2.0
HEART_LIFETIME = 1.2
HEART_COUNT = 3

# ── Chat input ──
CHAT_INPUT_WIDTH = 240
CHAT_INPUT_HEIGHT = 36
CHAT_PLACEHOLDER = "Say something..."
CHAT_TETHER_COLOR = (50, 30, 10, 120)
CHAT_TETHER_WIDTH = 1

# ── Onboarding ──
ONBOARDING_HINT_DURATION_MS = 4000
ONBOARDING_FADE_IN_MS = 300
ONBOARDING_FADE_OUT_MS = 500

# ── Hover / drag ──
HOVER_BOUNCE_AMP = 0.03
HOVER_BOUNCE_FREQ = 2.0
DRAG_OPACITY = 0.85
DRAG_SHADOW_COLOR = (0, 0, 0, 40)

# ── Edge feedback ──
EDGE_SQUASH_FACTOR = 0.75
EDGE_BOUNCE_RESTORE_MS = 200

# ── Groq chat ──
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.1-8b-instant"
CHAT_MAX_TOKENS = 60
CHAT_TEMPERATURE = 0.9
CHAT_COOLDOWN = 3.0
CHAT_TIMEOUT_SECONDS = 8

# ── Screen reading (fox "watches" the user's display) ──
SCREEN_READ_INTERVAL_S = 5.0       # capture cadence (every 5 s)
SCREEN_POLL_COARSE = (24, 14)      # downscale grid used for change detection
SCREEN_SIG_THRESHOLD = 640         # min abs-diff over the coarse grid to count as "changed"
SCREEN_VISION_MIN_INTERVAL_S = 10.0  # min seconds between vision API calls
SCREEN_SIMILAR_THRESHOLD = 0.82    # skip memory write if summary is ~this similar to last
SCREEN_VISION_MODEL = "llama-3.2-11b-vision-preview"
SCREEN_VISION_MAX_TOKENS = 40
SCREEN_SOURCE_LABEL = "I noticed on your screen"
# Fallback brain for screenshots when Groq is unavailable/fails.
# Uses OpenAI's vision API (the models that power ChatGPT) if OPENAI_API_KEY is set.
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
SCREEN_OPENAI_VISION_MODEL = "gpt-4o-mini"

# ── Settings file ──
SETTINGS_PATH = "settings.json"

_TMP_SUFFIX = ".tmp"

def load_settings():
    """Load persisted settings with recovery after a crashed write.

    Tries the primary path first; if that does not exist or parses as
    invalid JSON, falls back to the temp-file path ``SETTINGS_PATH +
    ".tmp"`` which may hold the last-good write when ``os.replace`` had
    not yet swapped the new atomically-written file on crash.
    """
    paths = [SETTINGS_PATH, SETTINGS_PATH + _TMP_SUFFIX]
    for path in paths:
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                continue
            if path != SETTINGS_PATH:
                try:
                    os.replace(path, SETTINGS_PATH)
                except OSError:
                    pass
            return data
        except (OSError, ValueError, json.JSONDecodeError):
            continue
    return {}


def save_settings(data: dict):
    """Atomically persist ``data`` as JSON.

    Writes the full payload to ``SETTINGS_PATH`` + ``".tmp"`` in the same
    directory (ensuring the same filesystem so ``os.replace`` is atomic),
    then swaps the temp file into place.  If the process crashes mid-write
    the original ``SETTINGS_PATH`` remains untouched, preserving the
    previous settings.
    """
    if not isinstance(data, dict):
        raise TypeError("save_settings() expects a dict, not %r" % type(data))
    tmp_path = SETTINGS_PATH + _TMP_SUFFIX
    directory = os.path.dirname(SETTINGS_PATH) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_path, SETTINGS_PATH)
    except Exception:
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass

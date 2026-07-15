import os
import json
from datetime import datetime
datetime = datetime  # expose as module attribute for config.datetime

from dotenv import load_dotenv
load_dotenv()

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
    now = datetime.now()
    for sh, sm, eh, em in MEAL_HOURS:
        start = now.replace(hour=sh, minute=sm, second=0)
        end = now.replace(hour=eh, minute=em, second=0)
        if start <= now <= end:
            return 1.5
    return 0.5

# ── Night hours ──
NIGHT_START_HOUR = 22
NIGHT_END_HOUR = 6

# ── Activity / idle (screen watcher) ──
IDLE_AFK_SECONDS = 120.0
IDLE_NAP_SECONDS = 300.0
NAP_FOCUS_RATE = 2.0
CODING_FOCUS_MULTIPLIER = 0.5

# ── Voice ──
VOICE_NAME = "en-IN-NeerjaNeural"
VOICE_RATE = "+10%"
VOICE_PITCH = "+15Hz"

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
BUBBLE_BG = (248, 244, 236, 240)
BUBBLE_BORDER = (50, 30, 10)
BUBBLE_TEXT_COLOR = (30, 20, 10)
BUBBLE_BG_HEX = "#F8F4EC"
BUBBLE_BORDER_HEX = "#321E0A"
BUBBLE_TEXT_HEX = "#1E140A"

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

# ── Groq chat ──
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
GROQ_MODEL = "llama-3.1-8b-instant"
CHAT_MAX_TOKENS = 60
CHAT_TEMPERATURE = 0.9
CHAT_COOLDOWN = 3.0
CHAT_TIMEOUT_SECONDS = 8

# ── Settings file ──
SETTINGS_PATH = "settings.json"

def load_settings():
    if not os.path.exists(SETTINGS_PATH):
        return {}
    try:
        with open(SETTINGS_PATH) as f:
            return json.load(f)
    except Exception:
        return {}

def save_settings(data: dict):
    try:
        with open(SETTINGS_PATH, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

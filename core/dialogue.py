import random
from core import config

def _get_wake_lines() -> list[str]:
    h = config.hour()
    if 5 <= h < 12:
        return ["Good morning!", "Morning sunshine!", "Rise and shine!"]
    elif 12 <= h < 17:
        return ["Good afternoon!", "Hey there!", "Oh, hi!"]
    else:
        return ["Good evening!", "Evening!", "Hey, still awake?"]

LINES = {
    "late": ["Zzz... getting sleepy.", "Time for bed soon...", "*yawn*"],
    "sit_tired": ["Phew, tired...", "Just a lil break.", "Resting my paws."],
    "jump_excited": ["Wheee!", "Boing!", "Yay!"],
    "walk_start": ["Off I go!", "Exploring time!", "Let's go!"],
    "poke_reaction": ["Hey!", "Ouch!", "Watch it!", "Stop that!"],
    "idle_bored": ["...", "So bored.", "Anything happening?"],
    "manual_sit": ["Okay, resting!", "Sure, I'll sit.", "Comfy spot!", "Alright, settling down."],
    "manual_walk": ["Let's go!", "Walking time!", "Alright, moving!", "Sure thing!"],
    "manual_jump": ["Wheee!", "Boing!", "As you wish!"],
    "manual_hit": ["Ouch! Why?", "Hey, that hurt!", "Rude!", "Okay, okay!"],
    "manual_idle": ["Just hanging out.", "Alright, chill mode.", "Okay, standing by."],
    "reluctant_walk": ["Fine, I'll walk...", "If you insist...", "*sigh* okay."],
    "reluctant_sit": ["I guess I'll sit...", "Fine, resting.", "Okay, sitting down."],
    "reluctant_idle": ["Fine, I'll stay.", "Okay, okay.", "Hmph."],
    "user_coding": ["Ooh, code!", "Working hard?", "Getting things done!", "Click clack!", "Type type!"],
    "user_browsing": ["Whatcha looking at?", "Scrolling again?", "Find anything interesting?", "Don't fall down the rabbit hole!"],
    "user_chatting": ["Who ya talking to?", "Ooh, gossip?", "Say hi for me!"] ,
    "user_afk": ["Hello?", "Where'd you go?", "*yawn*", "Come back..."],
    "user_back": ["You're back!", "Welcome back!", "Missed you!", "There you are!"],
    "user_focused": ["I'll be quiet.", "Focus mode!", "Shh, working.", "Don't mind me."],
    "user_nap": ["Zzz...", "*sleeping*", "Nap time...", "Mmm... sleepy..."],
    "user_wake": ["Oh, you're back!", "Rise and shine!", "Good timing!"],
    "mealtime": ["Is it snack time?", "Smells good!", "Food time?", "Tummy's ready!", "Breakfast time!", "Lunch time!", "Dinner time!", "Snack time!"],
}

def get_line(trigger: str) -> str:
    if trigger == "wake":
        pool = _get_wake_lines()
    else:
        pool = LINES.get(trigger)
    return random.choice(pool) if pool else ""

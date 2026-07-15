import random
from core import config
from core.logger import get_logger

log = get_logger("brain")

class Brain:
    def __init__(self):
        self.energy = config.ENERGY_INITIAL
        self.boredom = config.BOREDOM_INITIAL
        self.hunger = config.HUNGER_INITIAL_MEAL if config.meal_factor() > 1.0 else config.HUNGER_INITIAL_OFFPEAK
        self.activity_category = "other"
        self.user_idle_seconds = 0.0
        self.walk_speed = 0.5

    def update(self, dt, state):
        mf = config.meal_factor()
        focus = 1.0
        if self.activity_category == "coding" and self.user_idle_seconds < config.IDLE_AFK_SECONDS:
            focus = config.CODING_FOCUS_MULTIPLIER

        if self.user_idle_seconds >= config.IDLE_AFK_SECONDS:
            self.energy += config.NAP_FOCUS_RATE * dt
            self.boredom += config.BOREDOM_SIT_RATE * dt
            self.hunger += config.HUNGER_REST_RATE * dt
        else:
            if state == "idle":
                self.energy += config.ENERGY_IDLE_RATE * dt
                self.boredom += config.BOREDOM_IDLE_RATE * focus * dt
                self.hunger += config.HUNGER_PASSIVE_RATE * mf * dt
            elif state == "sit":
                self.energy += config.ENERGY_SIT_RATE * dt
                self.boredom += config.BOREDOM_SIT_RATE * focus * dt
                self.hunger += config.HUNGER_REST_RATE * mf * dt
            elif state == "walk":
                self.energy += config.ENERGY_WALK_RATE * dt
                self.boredom += config.BOREDOM_WALK_RATE * dt
                self.hunger += config.HUNGER_ACTIVE_RATE * mf * dt
            elif state == "jump":
                self.energy += config.ENERGY_JUMP_RATE * dt
                self.boredom += config.BOREDOM_JUMP_RATE * dt
                self.hunger += config.HUNGER_ACTIVE_RATE * mf * dt

        self.energy = max(0.0, min(100.0, self.energy))
        self.boredom = max(0.0, min(100.0, self.boredom))
        self.hunger = max(0.0, min(100.0, self.hunger))

    def decide(self):
        self.walk_speed = 0.5
        log.debug("needs — energy=%.0f boredom=%.0f hunger=%.0f idle=%.0fs",
                  self.energy, self.boredom, self.hunger, self.user_idle_seconds)

        if self.user_idle_seconds >= config.IDLE_NAP_SECONDS:
            log.info("decide -> sit (nap, idle=%.0fs)", self.user_idle_seconds)
            return "sit"
        if self.is_late():
            if self.energy < 60 or random.random() < 0.6:
                log.info("decide -> sit (night sleepy)")
                return "sit"
        if self.energy < config.ENERGY_SLEEP_THRESHOLD:
            log.info("decide -> sit (exhausted energy=%.0f)", self.energy)
            return "sit"
        if self.boredom > config.BOREDOM_WALK_THRESHOLD:
            self.walk_speed = 0.6 + 0.4 * min(1.0, (self.boredom - config.BOREDOM_WALK_THRESHOLD) / 30)
            log.info("decide -> walk (bored boredom=%.0f speed=%.2f)", self.boredom, self.walk_speed)
            return "walk"
        if self.hunger > config.HUNGER_SIT_THRESHOLD and random.random() < config.HUNGER_SIT_PROBABILITY:
            log.info("decide -> sit (hungry hunger=%.0f)", self.hunger)
            return "sit"
        if (self.boredom > config.BOREDOM_JUMP_THRESHOLD and self.energy > config.ENERGY_JUMP_THRESHOLD
                and random.random() < config.JUMP_PROBABILITY):
            log.info("decide -> jump (bored+energetic)")
            return "jump"
        if (self.energy > config.ENERGY_WALK_THRESHOLD and self.boredom > config.BOREDOM_WALK_TRIGGER
                and random.random() < config.WALK_PROBABILITY):
            self.walk_speed = 0.3 + 0.3 * (1.0 - self.boredom / 100)
            log.info("decide -> walk (slow wander)")
            return "walk"
        log.info("decide -> idle (content)")
        return "idle"

    def is_late(self) -> bool:
        h = config.datetime.now().hour
        return h >= config.NIGHT_START_HOUR or h < config.NIGHT_END_HOUR

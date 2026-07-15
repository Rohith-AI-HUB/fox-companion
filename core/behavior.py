import random, time
from PyQt6.QtCore import QTimer, QPoint
from PyQt6.QtWidgets import QApplication
from core.brain import Brain
from core import config
from core.dialogue import get_line
from core.logger import get_logger

log = get_logger("behavior")

class Behavior:
    def __init__(self, window, sprites, screen_w, screen_h, anchor_y, voice=None, bubble=None):
        self.window = window
        self.sprites = sprites
        self.screen_w, self.screen_h = screen_w, screen_h
        self.anchor_y = anchor_y
        self.state = "idle"
        self.continuous_walk = False
        self._manual_override = False
        self._manual_action = None
        self.brain = Brain()
        self.voice = voice
        self.bubble = bubble
        self._last_speak = time.time() - config.SPEAK_COOLDOWN
        self._last_activity_speak = 0.0
        self._activity_spoke = False
        self._suppress_speech_until = 0.0
        self.state_timer = QTimer()
        self.state_timer.timeout.connect(self.choose_state)
        self.state_timer.start(int(config.BRAIN_TICK_INTERVAL * 1000))
        self._override_timer = QTimer()
        self._override_timer.setSingleShot(True)
        self._override_timer.timeout.connect(self._clear_override)
        self.sprites.play("idle")
        self.watcher = None

    def set_watcher(self, watcher):
        self.watcher = watcher

    def set_continuous_walk(self, enabled, action=None):
        self.continuous_walk = enabled
        if enabled:
            self._manual_action = action or "walk"
            self.state = "walk"
            self.sprites.play("walk")
            left, right = self._screen_bounds()
            self.window.physics.walk_to(right)
        else:
            self._manual_action = action or self.state
            self._manual_override = True
            self._override_timer.start(config.OVERRIDE_DURATION_MS)

    def _clear_override(self):
        self._manual_override = False
        
    def suppress_speech_for(self, seconds: float):
        """Suppress speech for the given number of seconds"""
        self._suppress_speech_until = time.time() + seconds

    def _screen_bounds(self):
        center = self.window.pos() + QPoint(self.window.width() // 2, self.window.height() // 2)
        screen = QApplication.screenAt(center)
        if screen:
            g = screen.geometry()
            return g.x(), g.x() + g.width() - self.window.width()
        return 0, self.screen_w - self.window.width()

    def _speak_activity(self, trigger):
        if not self.voice or not self.bubble:
            return
        now = time.time()
        if now < self._suppress_speech_until:
            return
        if now - self._last_activity_speak < config.ACTIVITY_SPEAK_COOLDOWN:
            return
        line = get_line(trigger)
        if not line:
            return
        self._last_activity_speak = now
        self._activity_spoke = True
        log.info("speak_activity trigger=%s line=%s", trigger, line)
        pos = self.window.pos()
        self.bubble.show_text(line, pos.x() + self.window.width() // 2, pos.y() + config.MOUTH_Y)
        self.voice.speak(line)

    def _maybe_speak(self, trigger):
        if not self.voice or not self.bubble:
            return
        if self._activity_spoke:
            return
        now = time.time()
        if now < self._suppress_speech_until:
            return
        if now - self._last_speak < config.SPEAK_COOLDOWN:
            return
        if random.random() > config.SPEAK_CHANCE:
            return
        line = get_line(trigger)
        if not line:
            return
        self._last_speak = now
        log.info("maybe_speak trigger=%s line=%s", trigger, line)
        pos = self.window.pos()
        self.bubble.show_text(line, pos.x() + self.window.width() // 2, pos.y() + config.MOUTH_Y)
        self.voice.speak(line)

    def choose_state(self):
        self._activity_spoke = False
        now = time.time()
        if now < self._suppress_speech_until:
            return
        if self.watcher:
            self.watcher.poll()
            self.brain.activity_category = self.watcher.category
            self.brain.user_idle_seconds = self.watcher.idle_seconds
            t = self.watcher.transition
            if t:
                log.info("watcher transition=%s category=%s idle=%.0fs", t, self.watcher.category, self.watcher.idle_seconds)
            if t == "became_active":
                self._speak_activity("user_back")
            elif t == "became_idle":
                self._speak_activity("user_afk")
            elif t == "category_changed":
                key = {"coding": "user_coding", "browsing": "user_browsing",
                       "communication": "user_chatting", "other": "user_focused"}.get(self.watcher.category)
                if key:
                    self._speak_activity(key)

        if self.continuous_walk or self._manual_override:
            return
        self.brain.update(config.BRAIN_TICK_INTERVAL, self.state)
        prev_state = self.state
        new_state = self.brain.decide()
        if new_state != prev_state:
            log.info("state %s -> %s", prev_state, new_state)
        if self._manual_action and new_state != self._manual_action and random.random() < 0.5:
            key = "reluctant_" + new_state
            line = get_line(key)
            if line:
                pos = self.window.pos()
                self.bubble.show_text(line, pos.x() + self.window.width() // 2, pos.y() + config.MOUTH_Y)
                self.voice.speak(line)
        self._manual_action = None
        self.state = new_state
        p = self.window.physics
        left, right = self._screen_bounds()
        p.set_bounds(left, right)
        p.stop_walk()
        if self.state == "walk":
            target = right if p.x < (left + right) / 2 else left
            speed = config.MIN_WALK_SPEED + self.brain.walk_speed * (config.MAX_WALK_SPEED - config.MIN_WALK_SPEED)
            self.sprites.play("walk")
            p.walk_to(target, speed)
            if prev_state != "walk":
                self._maybe_speak("walk_start")
        elif self.state == "sit":
            self.sprites.play("sit", loop=False)
            self.sprites.on_finish = lambda: self.sprites.play("sit_idle")
            if self.watcher and self.watcher.is_napping():
                self._speak_activity("user_nap")
            elif self.brain.is_late():
                self._maybe_speak("late")
            elif config.meal_factor() > 1.0:
                self._maybe_speak("mealtime")
            else:
                self._maybe_speak("sit_tired")
        elif self.state == "jump":
            self.sprites.play("jump", loop=False)
            self.sprites.on_finish = lambda: self.sprites.play("idle")
            self.window.physics.jump(config.BRAIN_JUMP_VY, 0)
            self._maybe_speak("jump_excited")
        else:
            self.sprites.play("idle")
            if prev_state == "walk":
                self._maybe_speak("idle_bored")
            elif prev_state == "sit" and self.watcher and self.watcher.transition == "became_active":
                self._speak_activity("user_wake")
            elif config.meal_factor() > 1.0:
                self._maybe_speak("mealtime")

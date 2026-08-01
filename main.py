import sys, time, collections, os
from PyQt6.QtWidgets import QApplication, QSystemTrayIcon, QMenu
from PyQt6.QtGui import QIcon, QPixmap, QAction
from PyQt6.QtCore import Qt, QTimer
from core import config
from core.logger import get_logger

log = get_logger("main")
# Get the directory containing main.py to use for absolute asset paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

from ui.window import CompanionWindow
from ui.sprite_manager import SpriteManager
from core.behavior import Behavior
from core.physics import PhysicsState
from core.easing import idle_breathing
from ui.road_strip import RoadStrip
from foxio.voice import VoiceEngine
from ui.speech_bubble import SpeechBubble
from core.dialogue import get_line
from foxio.screen_watcher import ScreenWatcher
from ui.chat_input import ChatInput
from ui.particles import ParticleManager
from ui.onboarding import OnboardingHints
from foxio.fox_brain_llm import FoxLLM
from foxio.wake_listener import WakeListener
from foxio.voice_input import VoiceInput
from brain import FoxBrain

_pending_replies = collections.deque()

app = QApplication(sys.argv)
screen = app.primaryScreen()
sg = screen.geometry()
avail = screen.availableGeometry()
anchor_y = avail.y() + avail.height() - config.WINDOW_SIZE
log.info("started — screen %dx%d, anchor_y=%d", sg.width(), sg.height(), anchor_y)

physics = PhysicsState(max(120.0, sg.width() * 0.15), float(anchor_y), float(anchor_y), 0.0, float(sg.width() - config.WINDOW_SIZE))

settings = config.load_settings()

road = RoadStrip(sg.width(), tile_path=os.path.join(ASSETS_DIR, "road_tile.png"))
road.move(0, anchor_y + config.WINDOW_SIZE - config.ROAD_HEIGHT)
road.show()

win = CompanionWindow()
win.set_physics(physics)
win.move(int(physics.x), int(physics.y))
if settings.get("click_through", False):
    win.toggle_click_through()
win.show()

road.lower()
win.raise_()

voice = VoiceEngine()
voice.muted = settings.get("muted", False)
bubble = SpeechBubble()
bubble.show()

chat_input = ChatInput()
fox_llm = FoxLLM()
fox_brain = FoxBrain(user_id="default", vault_path="brain/vault")
voice_input = VoiceInput()
last_chat_time = [0.0]
voice_mode_enabled = settings.get("voice_mode", True)  # Can be toggled via settings

sprites = SpriteManager(win, assets_dir=os.path.join(ASSETS_DIR, "fox"))
win.set_sprites(sprites)
watcher = ScreenWatcher()
behavior = Behavior(win, sprites, sg.width(), sg.height(), anchor_y, voice=voice, bubble=bubble)
behavior.set_watcher(watcher)
win.set_behavior(behavior)
win.set_voice_bubble(voice, bubble)

particles = ParticleManager()
particles.set_anchor_window(win)
win.particles = particles

onboarding = OnboardingHints()
onboarding.start(win)

def get_time_of_day():
    h = config.hour()
    if h < 6: return "late night"
    if h < 12: return "morning"
    if h < 17: return "afternoon"
    if h < 21: return "evening"
    return "night"

def update_night_mode():
    is_night = config.is_night()
    bubble.set_night_mode(is_night)

update_night_mode()

def open_chat_input():
    if voice_mode_enabled:
        open_voice_chat()
    else:
        open_text_chat()

def open_text_chat():
    pos = win.pos()
    chat_input.open_at(pos.x() - 20, pos.y() - 40)
    behavior.suppress_speech_for(10)
    sprites.play("sit", loop=False)
    sprites.on_finish = lambda: sprites.play("sit_idle")
    physics.stop_walk()

def open_voice_chat():
    """Start voice input mode after wake word."""
    behavior.suppress_speech_for(10)
    sprites.play("sit", loop=False)
    sprites.on_finish = lambda: sprites.play("sit_idle")
    physics.stop_walk()
    
    # Show listening indicator
    pos = win.pos()
    bubble.show_text("Listening...", pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=8000)
    
    # Start voice input
    voice_input.listen(
        timeout=8.0,
        on_result=handle_voice_result,
        on_error=handle_voice_error
    )

_CHARS_PER_SECOND_READ = 18.0
_BUBBLE_MIN_MS = 2500
_BUBBLE_MAX_MS = 20000


def _estimate_bubble_ms(text: str, tts_hint_s: float = 0.0) -> int:
    """Bubble lifetime = max(text reading time, tts duration). Clamped to sane range."""
    read_s = max(1.0, len(text) / _CHARS_PER_SECOND_READ)
    total_s = max(read_s, float(tts_hint_s))
    ms = int(total_s * 1000) + 500  # small tail buffer
    return max(_BUBBLE_MIN_MS, min(_BUBBLE_MAX_MS, ms))


def _deliver_reply(reply_text: str):
    pos = win.pos()
    tts_hint_s = voice.last_duration() if voice else 0.0
    estimated_ms = _estimate_bubble_ms(reply_text, tts_hint_s)
    bubble.show_text(reply_text, pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=estimated_ms)

    suppress_s = min(15.0, estimated_ms / 1000.0)
    behavior.suppress_speech_for(suppress_s)

    if voice.muted:
        return

    def _on_end():
        try:
            bubble._hide_timer.stop()
            QTimer.singleShot(600, bubble._fade_out)
        except Exception:
            pass

    voice.speak(reply_text, on_end=_on_end)

def handle_chat_submit(text: str):
    now = time.time()
    if now - last_chat_time[0] < config.CHAT_COOLDOWN:
        pos = win.pos()
        bubble.show_text("One sec!", pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=1500)
        return
    last_chat_time[0] = now
    log.info("chat submit: %s", text)
    behavior.suppress_speech_for(15)
    sprites.play("sit", loop=False)
    sprites.on_finish = lambda: sprites.play("sit_idle")
    physics.stop_walk()

    # ── Capture user input to memory (background thread, fire & forget)
    def _capture_user_done(_facts):
        log.info("captured user input to memory")
    fox_brain.capture_async(text, user_id="default", on_done=_capture_user_done)

    # Show thinking indicator while retrieving memories
    pos = win.pos()
    bubble.show_thinking(pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y)

    # ── Retrieve relevant memories OFF main thread, then ask LLM ──
    def _on_retrieved(memories):
        memory_context = ""
        if memories and memories.get('merged'):
            memory_context = "Relevant things I remember: " + "; ".join(
                [m.get('content', '') for m in memories['merged']]
            )
            log.info("retrieved %d memories", len(memories['merged']))
        _ask_llm(memory_context)

    def _on_retrieve_err(err):
        log.error("memory retrieval async failed: %s", err)
        _ask_llm("")

    def _ask_llm(memory_context):
        brain_state = {
            "energy": behavior.brain.energy,
            "boredom": behavior.brain.boredom,
            "hunger": behavior.brain.hunger,
            "activity_category": behavior.watcher.category,
            "time_of_day": get_time_of_day(),
            "memory_context": memory_context,
        }

        def on_result(reply_text):
            bubble.hide_thinking()
            # Capture fox response in background (fire & forget)
            fox_brain.capture_async(reply_text, user_id="default",
                on_done=lambda _fs: log.info("captured fox response to memory"))
            _pending_replies.append(reply_text)

        def on_error(err):
            bubble.hide_thinking()
            if err == "no_api_key":
                _pending_replies.append("I can't think right now... no API key set!")
            else:
                _pending_replies.append("Hmm, brain freeze. Try again?")

        fox_llm.ask(text, brain_state, on_result=on_result, on_error=on_error)

    fox_brain.retrieve_async(text, user_id="default", top_k=3,
        on_done=_on_retrieved, on_error=_on_retrieve_err)

def handle_voice_result(text: str):
    """Handle transcribed voice input."""
    log.info("Voice result: %s", text)
    
    # Check for exit commands
    exit_commands = ["cancel", "never mind", "forget it", "stop", "exit"]
    if any(cmd in text.lower() for cmd in exit_commands):
        pos = win.pos()
        bubble.show_text("Okay!", pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=2000)
        return
    
    # Process the voice input same as text input
    handle_chat_submit(text)

def handle_voice_error(error: str):
    """Handle voice input errors."""
    log.warning("Voice error: %s", error)
    
    error_messages = {
        "timeout": "I didn't hear anything.",
        "could_not_understand": "I couldn't understand that.",
        "microphone_error": "Microphone not working.",
        "service_error": "Voice service unavailable.",
        "unknown_error": "Something went wrong."
    }
    
    message = error_messages.get(error, "Sorry, try again.")
    pos = win.pos()
    bubble.show_text(message, pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=3000)
    
    # Fallback to text input on error
    if error in ["microphone_error", "service_error"]:
        QTimer.singleShot(1000, open_text_chat)

chat_input.submitted.connect(handle_chat_submit)
win.open_chat = open_chat_input

def on_wake_detected():
    log.info("wake!")
    # Capture wake word event to memory (fire & forget, background thread)
    fox_brain.capture_async(
        "User said 'Hey Fox' to get my attention",
        user_id="default",
        on_done=lambda _: log.info("captured wake word event to memory"),
    )

    pos = win.pos()
    bubble.show_text("Yes?", pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=3000)
    behavior.suppress_speech_for(10)
    sprites.play("jump", loop=False)
    sprites.on_finish = lambda: sprites.play("idle")

    # Initialize microphone if not already done
    if not voice_input.microphone:
        voice_input.initialize_microphone()

    # Open voice chat after short delay
    QTimer.singleShot(1500, open_voice_chat)

wake_listener = WakeListener(on_wake=on_wake_detected)
wake_listener.start()

# Kick off FastEmbed + Chroma warmup on a background thread AFTER first frame
# so startup latency isn't eaten by model downloads/loading.
QTimer.singleShot(250, fox_brain.start_embedding_warmup)

QTimer.singleShot(1000, lambda: (
    bubble.show_text(get_line("wake"), win.x() + win.width() // 2, win.y() + config.MOUTH_Y),
    voice.speak(get_line("wake"))
))

render_timer = QTimer()
render_timer.setTimerType(Qt.TimerType.PreciseTimer)

prev_time = time.perf_counter()
breath_time = 0.0

def render_loop():
    global prev_time, breath_time
    now = time.perf_counter()
    dt = min(now - prev_time, 0.05)
    prev_time = now
    breath_time += dt

    phys = win.physics
    if win.dragging:
        phys.x = float(win.x())
        phys.y = float(win.y())
    elif win._just_released:
        win._just_released = False
        phys.release_from_drag(float(win.x()), float(win.y()),
                                win._release_vx, win._release_vy)
        win._release_vx = 0.0
        win._release_vy = 0.0

    phys.step(dt)

    particles.update_particles(dt)
    road.scroll_offset = phys.x * 0.3

    pos = win.pos()

    if phys.just_landed:
        particles.spawn_landing_puff(pos.x() + win.width() // 2, pos.y() + win.height())

    if phys.edge_hit:
        particles.spawn_bonk_stars(pos.x() + win.width() // 2, pos.y() + win.height() // 2)

    if phys.walking and phys.on_ground and abs(phys.vx) > 20:
        particles._dust_timer += dt
        if particles._dust_timer >= config.DUST_SPAWN_INTERVAL:
            particles._dust_timer = 0
            dir = -1 if phys.vx > 0 else 1
            particles.spawn_dust(pos.x() + win.width() // 2 + dir * 10, pos.y() + win.height())
    else:
        particles._dust_timer = 0

    if behavior.state == "idle" and behavior.brain.boredom > 60:
        particles._zzz_timer += dt
        if particles._zzz_timer >= config.Zzz_SPAWN_INTERVAL:
            particles._zzz_timer = 0
            particles.spawn_zzz(pos.x() + win.width() // 2, pos.y())
    else:
        particles._zzz_timer = 0

    while _pending_replies:
        _deliver_reply(_pending_replies.popleft())

    if phys.on_ground and not win.dragging:
        win._transform_squash = phys.squash
        breath = idle_breathing(breath_time)
        if not phys.walking:
            win._transform_scale = breath
        else:
            win._transform_scale = 1.0
    else:
        win._transform_scale = 1.0

    if phys.vx > 5.0:
        sprites.direction = 1
    elif phys.vx < -5.0:
        sprites.direction = -1

    sprites.advance(dt)

    wx = int(round(phys.x))
    wy = int(round(phys.y))
    if win.x() != wx or win.y() != wy:
        win.move(wx, wy)

    win.update()

render_timer.timeout.connect(render_loop)
render_timer.start(16)

tray = QSystemTrayIcon(QIcon(QPixmap(os.path.join(ASSETS_DIR, "fox", "idle_1.png")).scaled(32, 32)), app)
tray.setToolTip("Fox Companion")

tray_menu = QMenu()

toggle_vis = QAction("Hide", tray_menu)
def toggle_visible():
    if win.isVisible():
        win.hide()
        toggle_vis.setText("Show")
    else:
        win.show()
        toggle_vis.setText("Hide")
toggle_vis.triggered.connect(toggle_visible)

mute_act = QAction("Mute Voice", tray_menu)
mute_act.setCheckable(True)
mute_act.setChecked(voice.muted)
def toggle_mute(checked):
    voice.muted = checked
    config.save_settings({"muted": checked, "click_through": win.click_through, "voice_mode": voice_mode_enabled})
mute_act.triggered.connect(toggle_mute)

click_thru = QAction(f"Click-Through: {'On' if settings.get('click_through', False) else 'Off'}", tray_menu)
def toggle_click_thru():
    on = win.toggle_click_through()
    click_thru.setText(f"Click-Through: {'On' if on else 'Off'}")
    config.save_settings({"muted": voice.muted, "click_through": on})
click_thru.triggered.connect(toggle_click_thru)

chat_act = QAction("Talk to Fox", tray_menu)
chat_act.triggered.connect(open_chat_input)

voice_mode_act = QAction(f"Voice Mode: {'On' if voice_mode_enabled else 'Off'}", tray_menu)
voice_mode_act.setCheckable(True)
voice_mode_act.setChecked(voice_mode_enabled)
def toggle_voice_mode(checked):
    global voice_mode_enabled
    voice_mode_enabled = checked
    voice_mode_act.setText(f"Voice Mode: {'On' if checked else 'Off'}")
    config.save_settings({"muted": voice.muted, "click_through": win.click_through, "voice_mode": voice_mode_enabled})
voice_mode_act.triggered.connect(toggle_voice_mode)

# ── Centralized shutdown ────────────────────────────────────────────

_SHUTDOWN_DONE = False


def run_shutdown():
    """Teardown every long-running subsystem in a safe order.

    Idempotent: called from ``app.aboutToQuit`` so every exit path (tray
    Quit, window close, process signal) runs cleanup.  Also flushes log
    handlers so the final messages are written to disk.
    """
    global _SHUTDOWN_DONE
    if _SHUTDOWN_DONE:
        return
    _SHUTDOWN_DONE = True
    log.info("shutdown: persisting settings…")
    try:
        config.save_settings({
            "muted": voice.muted,
            "click_through": win.click_through,
            "voice_mode": voice_mode_enabled,
        })
    except Exception as e:
        log.error("shutdown: save_settings failed: %s", e)

    log.info("shutdown: stopping wake listener…")
    try:
        wake_listener.stop()
    except Exception as e:
        log.error("shutdown: wake_listener.stop failed: %s", e)

    log.info("shutdown: stopping voice input…")
    try:
        voice_input.stop()
    except Exception as e:
        log.error("shutdown: voice_input.stop failed: %s", e)

    log.info("shutdown: stopping voice engine…")
    try:
        voice.shutdown()
    except Exception as e:
        log.error("shutdown: voice.shutdown failed: %s", e)

    log.info("shutdown: closing long-term memory…")
    try:
        fox_brain.close()
        log.info("shutdown: FoxBrain closed successfully")
    except Exception as e:
        log.error("shutdown: FoxBrain close failed: %s", e)

    # Flush + close every logger so RotatingFileHandler finishes writes
    import logging as _logging
    for handler in list(_logging.getLogger().handlers or []):
        try:
            handler.flush()
            handler.close()
        except Exception:
            pass
    log.info("shutdown complete")


app.aboutToQuit.connect(run_shutdown)


# ── Tray menu ───────────────────────────────────────────────────────

stop_speak_act = QAction("Stop Speaking", tray_menu)
stop_speak_act.setShortcut("Esc")

def _on_stop_speaking():
    voice.stop()
    bubble.hide_thinking()
    bubble._hide_timer.stop()
    try:
        bubble._fade_out()
    except Exception:
        pass
    behavior.suppress_speech_for(1)

stop_speak_act.triggered.connect(_on_stop_speaking)

quit_act = QAction("Quit", tray_menu)

def do_quit():
    app.quit()

quit_act.triggered.connect(do_quit)

hotkey_act = QAction("Register Global Hotkey (Admin)", tray_menu)

def _register_hotkey(_attempted=[False]):
    """Lazy-load ``keyboard`` (needs admin on Windows) and bind Ctrl+Alt+F.

    The module is intentionally imported inside the function: importing at
    module top-level would silently fail for non-admin users and the user
    had no way to retry.  Users who launch with admin once can later
    re-trigger this action without admin if UAC has already elevated.
    """
    try:
        import keyboard  # noqa: F401 — imported for module side-effect
    except Exception as exc:
        log.warning("hotkey: 'keyboard' module could not be imported: %s", exc)
        return
    try:
        keyboard.add_hotkey("ctrl+alt+f", lambda: QTimer.singleShot(0, open_chat_input))
        log.info("hotkey: registered ctrl+alt+f -> open chat")
        hotkey_act.setEnabled(False)
        hotkey_act.setText("Global Hotkey: Registered (Ctrl+Alt+F)")
    except Exception as exc:
        log.warning("hotkey: registration failed (admin may be required): %s", exc)

hotkey_act.triggered.connect(_register_hotkey)

# Build final menu order
tray_menu.addAction(toggle_vis)
tray_menu.addSeparator()
tray_menu.addAction(mute_act)
tray_menu.addSeparator()
tray_menu.addAction(click_thru)
tray_menu.addSeparator()
tray_menu.addAction(voice_mode_act)
tray_menu.addSeparator()
tray_menu.addAction(stop_speak_act)
tray_menu.addSeparator()
tray_menu.addAction(chat_act)
tray_menu.addAction(hotkey_act)
tray_menu.addSeparator()
tray_menu.addAction(quit_act)
tray.setContextMenu(tray_menu)
tray.show()

# Best-effort initial hotkey registration.  If this fails (no admin,
# unsupported OS, odd keyboard configuration) the user can still try again
# via the tray menu item.
_register_hotkey()

sys.exit(app.exec())

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
from foxio.fox_brain_llm import FoxLLM

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
last_chat_time = [0.0]

sprites = SpriteManager(win, assets_dir=os.path.join(ASSETS_DIR, "fox"))
win.set_sprites(sprites)
watcher = ScreenWatcher()
behavior = Behavior(win, sprites, sg.width(), sg.height(), anchor_y, voice=voice, bubble=bubble)
behavior.set_watcher(watcher)
win.set_behavior(behavior)
win.set_voice_bubble(voice, bubble)

def get_time_of_day():
    h = config.datetime.now().hour
    if h < 6: return "late night"
    if h < 12: return "morning"
    if h < 17: return "afternoon"
    if h < 21: return "evening"
    return "night"

def open_chat_input():
    pos = win.pos()
    chat_input.open_at(pos.x() - 20, pos.y() - 40)
    behavior.suppress_speech_for(10)
    sprites.play("sit", loop=False)
    sprites.on_finish = lambda: sprites.play("sit_idle")
    physics.stop_walk()

def _deliver_reply(reply_text: str):
    pos = win.pos()
    bubble.show_text(reply_text, pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=15000)
    if not voice.muted:
        voice.speak(reply_text)
    behavior.suppress_speech_for(15)

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

    brain_state = {
        "energy": behavior.brain.energy,
        "boredom": behavior.brain.boredom,
        "hunger": behavior.brain.hunger,
        "activity_category": behavior.watcher.category,
        "time_of_day": get_time_of_day(),
    }

    pos = win.pos()
    bubble.show_text("...", pos.x() + win.width() // 2, pos.y() + config.MOUTH_Y, duration_ms=0)

    def on_result(reply_text):
        _pending_replies.append(reply_text)

    def on_error(err):
        if err == "no_api_key":
            _pending_replies.append("I can't think right now... no API key set!")
        else:
            _pending_replies.append("Hmm, brain freeze. Try again?")

    fox_llm.ask(text, brain_state, on_result=on_result, on_error=on_error)

chat_input.submitted.connect(handle_chat_submit)
win.open_chat = open_chat_input

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
    config.save_settings({"muted": checked, "click_through": win.click_through})
mute_act.triggered.connect(toggle_mute)

click_thru = QAction("Click-Through: Off", tray_menu)
if settings.get("click_through", False):
    click_thru.setText("Click-Through: On")
def toggle_click_thru():
    on = win.toggle_click_through()
    click_thru.setText(f"Click-Through: {'On' if on else 'Off'}")
    config.save_settings({"muted": voice.muted, "click_through": on})
click_thru.triggered.connect(toggle_click_thru)

chat_act = QAction("Talk to Fox", tray_menu)
chat_act.triggered.connect(open_chat_input)

quit_act = QAction("Quit", tray_menu)
def do_quit():
    config.save_settings({"muted": voice.muted, "click_through": win.click_through})
    app.quit()
quit_act.triggered.connect(do_quit)

tray_menu.addAction(toggle_vis)
tray_menu.addSeparator()
tray_menu.addAction(mute_act)
tray_menu.addSeparator()
tray_menu.addAction(click_thru)
tray_menu.addSeparator()
tray_menu.addAction(chat_act)
tray_menu.addSeparator()
tray_menu.addAction(quit_act)
tray.setContextMenu(tray_menu)
tray.show()

# NOTE: The keyboard module may require elevated/admin privileges
# This can be a security consideration. Use with caution!
import keyboard
try:
    keyboard.add_hotkey("ctrl+alt+f", lambda: QTimer.singleShot(0, open_chat_input))
except Exception as exc:
    print(f"[main] global hotkey not available: {exc}")

sys.exit(app.exec())

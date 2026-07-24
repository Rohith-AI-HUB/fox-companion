# Fox-Companion Comprehensive Improvement Plan

**Date:** 2026-07-24
**Scope:** Technical Enhancements · Performance Optimizations · User Experience Improvements

---

## 1. Repo Research Conclusion

The fox-companion project is a desktop pet application built with PyQt6 that combines a virtual companion sprite with AI-powered conversational capabilities, memory management, activity awareness, and voice interaction. The codebase contains **19 Python modules** organized into 5 logical layers:

| Layer | Modules | Lines | Purpose |
|-------|---------|-------|---------|
| Entry | `main.py` | 425 | App bootstrap, render loop, event orchestration |
| Core AI | `core/brain.py`, `core/behavior.py`, `core/dialogue.py`, `core/config.py` | 500+ | Needs-based state machine, activity triggers, dialogue DB |
| Memory | `brain/brain.py` | 572 | ChromaDB+SQLite FTS hybrid memory, conflict resolution |
| I/O | `foxio/voice.py`, `foxio/voice_input.py`, `foxio/wake_listener.py`, `foxio/screen_watcher.py`, `foxio/fox_brain_llm.py`, `foxio/vad.py` | 500+ | TTS, STT, wake-word, activity tracking, LLM gateway |
| UI | `ui/window.py`, `ui/speech_bubble.py`, `ui/chat_input.py`, `ui/particles.py`, `ui/sprite_manager.py`, `ui/onboarding.py`, `ui/road_strip.py` | 700+ | Sprite rendering, bubbles, input, effects, overlays |
| Util | `core/physics.py`, `core/easing.py`, `core/logger.py` | 170+ | Physics simulation, easing curves, logging infra |

### Strengths (Preserve)
- Clean separation between core/AI logic and UI
- Platform guards in `screen_watcher.py` for Windows-only APIs
- Temporal validity model in `FoxBrain` for conflict-aware memory
- Hybrid retrieval (semantic + exact-match) in memory subsystem
- Activity-aware behavior via ScreenWatcher + brain state coupling

### Architecture Weaknesses
- **`main.py` is a monolithic God object** (425 lines): owns rendering, state, callbacks, tray, wiring, and I/O coordination. No dedicated `App` or `CompanionApp` coordinator class.
- **No centralized lifecycle management**: cleanup is ad-hoc (only `fox_brain.close()` is called in `do_quit`). Voice threads, wake listener, audio streams are not explicitly shut down.
- **Synchronous blocking on main thread**: `handle_chat_submit` calls `fox_brain.capture()` and `fox_brain.retrieve()` synchronously — these hit Groq (entity extraction, contradiction detection) and ChromaDB embedding generation, blocking the render loop.
- **Duplicate Brain concepts**: `core/brain.py` (needs/energy state machine) and `brain/brain.py` (long-term memory) share the `Brain` name. This creates cognitive load and naming conflicts.

---

## 2. Critical Pain Points Catalogue

### 2.1 Technical Debt (Priority: P0 — Fix first)

| # | Issue | Location | Impact |
|---|-------|----------|--------|
| T1 | **SQL injection vulnerability**: F-string concatenation builds `valid_ids_str` and `clean_query` directly into SQL. FTS query in `_fts_search` is not parameterized. | `brain/brain.py:L480-L546` | Security risk — malicious or edge-case fact content can corrupt DB, drop tables, or exfiltrate data. |
| T2 | **Monolithic `main.py`**: Render loop, tray menu, hotkey listener, chat callbacks, settings persistence, sprite/particle animation are all in one file. | `main.py:L1-L425` | Readability: 4/10. Testability: 0/10. Adding features (e.g., a new action) requires touching 5+ different sections of the same file. |
| T3 | **No graceful shutdown**: `WakeListener.stop()` exists but is never called. Audio streams via `sounddevice.InputStream` and `winsound` are abruptly terminated on exit. Voice threads are daemon-only. | `main.py:L393-L401` | Occasional temporary file leaks (`tmp_wav`, `tmp_mp3`), port locking on audio devices, potential data loss if SQLite write in progress. |
| T4 | **`keyboard` module imported unconditionally**: requires elevated/admin privileges globally. Failure is caught but only with `print`, not logger. | `main.py:L419-L423` | Silent failure in user environments without admin. Standard output is never visible in packaged GUI apps. |
| T5 | **`datetime` assignment hack**: `datetime = datetime` in `config.py` to "expose as module attribute". Also, `_hour()` in `dialogue.py` duplicates `is_late()` logic. | `config.py:L4`, `dialogue.py:L4-L5` | Confuses linters, creates two code paths for time-of-day checks (bug risk). |
| T6 | **Non-atomic settings writes**: `save_settings()` with `json.dump(data, f)` can produce partial `settings.json` on crash mid-write. | `config.py:L204-L209` | Settings loss (muted state, voice mode, click-through) after unclean shutdown. |

### 2.2 Performance Bottlenecks (Priority: P1)

| # | Issue | Location | Benchmark Estimate |
|---|-------|----------|--------------------|
| P1 | **`SpriteManager._count()` calls `os.listdir()` on EVERY `play()`** — walks directory each time animation changes. | `ui/sprite_manager.py:L26-L29` | 6-12 directory syscalls per state change (average 4 actions/min = 240 syscalls/hour). |
| P2 | **No pixmap preloading**: `QPixmap(path)` is recreated every frame from disk by `set_frame()`. 24 sprites × 8 FPS = ~192 disk reads/sec during walk animation. | `ui/window.py:L36-L39`, `ui/sprite_manager.py:L31-L46` | HDD users: stutter. SSD users: unnecessary I/O, higher power draw. |
| P3 | **`FoxBrain.capture()` performs 2 synchronous Groq API calls** (entity extraction + contradiction) on the main thread inside `handle_chat_submit`. | `brain/brain.py:L121-L152`, `L286-L316`, `main.py:L152-L166` | Latency: 800–2000ms added before LLM request even starts. UI freezes during this window. |
| P4 | **Soft shadow rendered per-frame with CPU loop**: 4 nested opacity layers painted manually. | `ui/speech_bubble.py:L165-L175` | ~4x draw calls per speech bubble repaint. Causes frame drops on integrated GPUs. |
| P5 | **`FoxLLM.ask()` lock is too coarse**: `_ask_sync` holds `self._lock` for entire Groq call (~300–2000ms). Precludes concurrent user-initiated vs triggered speech. | `foxio/fox_brain_llm.py:L46-L76` | User chat "brain freeze" if a behavior-triggered line is queued. |
| P6 | **`VoiceEngine._run()` synthesizes TTS every single call**: identical lines (e.g., "Good morning!") hit edge-tts network + mp3 decode + wav write → wav play every time. | `foxio/voice.py:L25-L71` | Common wake/greeting lines take 600–1200ms each time; 30-50% could be cache hits. |
| P7 | **Chroma + FastEmbed initialized at import**: FastEmbed downloads/loads BAAI/bge-small-en-v1.5 (~130MB) at startup if not cached. | `brain/brain.py:L44-L66` | First-run startup delay: 2–8s depending on network. Memory overhead: ~200-400MB resident. |

### 2.3 User Experience Issues (Priority: P1)

| # | Issue | Location | User Impact |
|---|-------|----------|-------------|
| U1 | **No chat history / transcript**: replies appear once and fade. User cannot review what was said 10 seconds ago. | `main.py:L131-L137` (reply delivery is fire-and-forget via deque) | Confusion on multi-turn answers, inability to copy-paste code snippets or facts fox mentioned. |
| U2 | **TTS/bubble duration mismatch**: Speech bubble is fixed-duration (15,000ms) while actual speech duration varies. Short replies linger; long replies are cut off visually. | `main.py:L132-L136` ("duration_ms=15000" hardcoded) | Breaks immersion. If user reads fast, bubble is a distraction. If slow, content vanishes mid-read. |
| U3 | **No "stop speaking" / interrupt**: Once voice starts, it cannot be cancelled. No button, no hotkey, no double-click to silence. | `foxio/voice.py:L22-L24` (stop exists but is never exposed to UI) | Annoying when long sentence triggered accidentally. Can't interrupt fox in mid-speech to ask something new. |
| U4 | **Voice input records FIXED `timeout` seconds** (8s) regardless of when user stops talking. No VAD-gated endpointing. | `foxio/voice_input.py:L50-L93` | User finishes sentence at 2s but waits 6s of silence. Poor UX; wastes time per voice query. |
| U5 | **Screen watcher categorization is keyword-only** (window title substring). Misses cases where "Chrome" is used for a web app IDE, or "VS Code" shows a document title without ".py". | `foxio/screen_watcher.py:L21-L90` | Inaccurate activity state = wrong behavior triggers (e.g., "user_browsing" commentary while actually coding). |
| U6 | **No settings GUI**: Tray actions only expose 5 binary toggles. Voice selection (currently hardcoded `en-IN-NeerjaNeural`), hotkey, bubble font size, particle density, brain tick rate, all hidden. | `main.py:L349-L415` | Non-technical users cannot personalize. Every config change currently requires code edit + restart. |
| U7 | **Multi-monitor / DPI issues**: Anchor and `RoadStrip` use `sg = primaryScreen.geometry()` only. Companion cannot walk onto a second monitor. | `main.py:L34-L46`, `ui/road_strip.py:L7-L19` | Multi-monitor users see a truncated road and pet stuck on primary. |
| U8 | **No visual "listening" state for voice mode**: Only bubble text "Listening...". No VU meter, no waveform, no pulsing indicator. Silence detection errors produce delayed feedback. | `main.py:L120-L129`, `foxio/voice_input.py` | Users unsure if mic is live; frequently repeat themselves or wait too long. |
| U9 | **Memory vault visibility**: Users have zero visibility into what fox remembers. No UI entry point to list, edit, or delete captured facts. | `brain/vault/raw/` directory (file-based) | Eerie feeling when fox references unknown memory. No way to correct misremembered facts. |

### 2.4 Cross-Platform & Reliability Gaps (Priority: P2)

| # | Issue | Location | Affected Platforms |
|---|-------|----------|--------------------|
| R1 | **TTS uses Windows-only `winsound`**. No macOS/Linux audio backend. | `foxio/voice.py:L1-L3`, `L22-L24` | macOS, Linux: complete silence. |
| R2 | **`winsound.PlaySound(SND_ASYNC)` playback holds lock until `time.sleep(dur)` expires** — `stop()` can cut playback but thread still blocks until duration. | `foxio/voice.py:L54-L55` | Playback not really cancellable; after stop, next speak blocked until old thread wakes. |
| R3 | **No `.env` validation**: Missing `GROQ_API_KEY` prints "no_api_key" line once, but no setup wizard or onboarding to guide the user. | `foxio/fox_brain_llm.py:L36-L39`, `core/config.py:L185` | First-time users: fox says "I can't think right now..." and they have no idea how to fix it. |
| R4 | **Wake listener error handling**: Any exception in `_run()` sets `running=False` silently. After one audio glitch, wake word is dead until app restart. | `foxio/wake_listener.py:L61-L74` | Subtle — user thinks wake word just has poor recognition. |
| R5 | **`ParticleManager` hardcoded resize(1920, 1080) on init**, then recalculates — initial paint wastes pixels. | `ui/particles.py:L53-L54` | Minor memory + paint waste on every launch. |

---

## 3. Prioritized Implementation Steps with Milestones

### Phase 1: Foundations & Hardening (P0 Security + Stability)
**Estimated modules touched**: `brain/brain.py`, `config.py`, `main.py`, `foxio/*`

#### Step 1.1 — Fix SQL Injection in Memory Layer
- **Refactor**: Replace f-string SQL assembly in `_fts_search` with parameterized queries. Use `?` placeholders for `user_id` and `limit`; for `valid_ids IN (...)` clause, build placeholder sets dynamically. Sanitize FTS MATCH tokens with proper escaping (use `FTS5` tokenizer rules).
- **Files**: [brain/brain.py:L480-L546](file:///c:/Users/rohit/Documents/fox-companion/brain/brain.py#L480-L546)
- **Risk level**: Medium (SQL change requires regression test)
- **Verification**: Create a fact containing `" '); DROP TABLE facts; --` and verify memory still retrieves correctly after.

#### Step 1.2 — Atomic Settings Persistence
- **Refactor**: `save_settings()` writes to `settings.json.tmp` in the same directory, then `os.replace()` (atomic on Windows NTFS and Unix).
- **Files**: [config.py:L204-L209](file:///c:/Users/rohit/Documents/fox-companion/core/config.py#L204-L209)
- **Add**: `load_settings()` fallback: if JSON fails, try `settings.json.tmp` (recovery scenario).
- **Verification**: Kill process mid-write, verify next start loads last-good state.

#### Step 1.3 — Graceful Shutdown Manager
- **New**: Add `register_shutdown_handler(callable)` pattern in `core/shutdown.py` (or inline in main). Ensure `app.aboutToQuit` signal fires: `wake_listener.stop()`, `voice_input.stop()`, `voice.stop()`, `fox_brain.close()`, flush logs.
- **Files**: [main.py:L393-L401](file:///c:/Users/rohit/Documents/fox-companion/main.py#L393-L401), [foxio/voice.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/voice.py), [foxio/wake_listener.py:L56-L60](file:///c:/Users/rohit/Documents/fox-companion/foxio/wake_listener.py#L56-L60)
- **Add**: `VoiceEngine.stop()` should not just `winsound.SND_PURGE` — also set a cancelled flag so `_run` thread exits `time.sleep` loop immediately via polling.
- **Verification**: Start a long TTS, trigger Quit via tray. Confirm process exits in <2s, no tmp_* files remain.

#### Step 1.4 — Cleanup `datetime` Duplication & Naming
- **Refactor**: Remove `datetime = datetime` alias hack in `config.py`; expose `def now() -> datetime` helper. Update `config.is_night()`, `config.meal_factor()`, `core/brain.py:is_late()`, `dialogue.py:_hour()` to use the same source.
- **Files**: [config.py:L1-L5](file:///c:/Users/rohit/Documents/fox-companion/core/config.py#L1-L5), [core/brain.py:L82-L84](file:///c:/Users/rohit/Documents/fox-companion/core/brain.py#L82-L84), [core/dialogue.py:L1-L6](file:///c:/Users/rohit/Documents/fox-companion/core/dialogue.py#L1-L6)
- **Rename** (backward-compat aliases OK for one release): `core/brain.py:Brain` → `NeedsBrain`; `brain/brain.py:FoxBrain` → `LongTermMemory`. Alias old names with deprecation warnings.

#### Step 1.5 — Hotkey & Privilege Handling
- **Refactor**: Move `import keyboard` to lazy import inside a try/except that logs via `get_logger("hotkey")` instead of `print`. Add tray menu item "Register Global Hotkey (Admin)" that attempts re-import with instructions.
- **Files**: [main.py:L419-L423](file:///c:/Users/rohit/Documents/fox-companion/main.py#L419-L423)

---

### Phase 2: Performance (P1)

#### Step 2.1 — Sprite & Pixmap Caching
- **Refactor**: `SpriteManager.__init__` pre-discovers all action frame counts into a dict. Build an LRU `QPixmap` cache keyed by frame path (max 32 entries; each fox sprite ~10KB so 320KB total).
- **Files**: [ui/sprite_manager.py:L1-L48](file:///c:/Users/rohit/Documents/fox-companion/ui/sprite_manager.py#L1-L48), [ui/window.py:L36-L39](file:///c:/Users/rohit/Documents/fox-companion/ui/window.py#L36-L39)
- **Change**: `set_frame(path, direction)` calls `cache.get(path)` — load-on-miss with `QPixmap` stored.
- **Expected gain**: 0 disk I/O during walking. CPU reduction in render loop: ~8-12%.

#### Step 2.2 — Asynchronous Memory Pipeline (Main Thread Unblocking)
- **Refactor**: `handle_chat_submit` calls `fox_brain.capture_async(text, callback)` and `fox_brain.retrieve_async(query, callback)` via thread-pool. Groq entity extraction + contradiction happen off-main. LLM call starts as soon as memory retrieval returns; capture happens in parallel (don't block UI on capture).
- **Files**: [main.py:L138-L196](file:///c:/Users/rohit/Documents/fox-companion/main.py#L138-L196), [brain/brain.py:L154-L226](file:///c:/Users/rohit/Documents/fox-companion/brain/brain.py#L154-L226), [brain/brain.py:L399-L461](file:///c:/Users/rohit/Documents/fox-companion/brain/brain.py#L399-L461)
- **Caution**: SQLite connections must be per-thread (or use `check_same_thread=False` carefully) or use a dedicated DB worker thread with queue.
- **Expected gain**: Chat-to-first-byte latency reduced by 800–2000ms. Render loop never blocks.

#### Step 2.3 — TTS Caching Layer + Interruptible Playback
- **Refactor**: Hash `(text, voice_name, rate, pitch)` with SHA-256 → cache wav file in `%TEMP%/fox-tts-cache/` with LRU eviction (max 50 MB, or age out >7 days). Cache hits skip network+decode entirely.
- **Refactor**: Replace `time.sleep(dur)` blocking playback with `miniaudio` playback (already imported!) that uses its own async `Device` — supports true `stop()` with immediate cancel.
- **Files**: [foxio/voice.py:L1-L78](file:///c:/Users/rohit/Documents/fox-companion/foxio/voice.py#L1-L78)
- **Expose**: Add tray + double-click context menu item "Stop Speaking" (also wire to escape key when chat open).
- **Expected gain**: Greeting/common lines playback <50ms vs 600–1200ms. True cancellation support for U3.

#### Step 2.4 — Soft Shadow Optimization (Single Pass)
- **Refactor**: Replace 4-iteration opacity shadow loop with single precomputed drop-shadow using `QGraphicsDropShadowEffect` attached to an internal child widget, OR pre-render the bubble body + shadow to a cached `QPixmap` and redraw only when text/colors change.
- **Files**: [ui/speech_bubble.py:L144-L206](file:///c:/Users/rohit/Documents/fox-companion/ui/speech_bubble.py#L144-L206)
- **Expected gain**: 3x fewer painter ops per speech bubble repaint. Frame rate up on low-end devices.

#### Step 2.5 — Lazy FastEmbed + Warmup in Background
- **Refactor**: Move `FastEmbed` model initialization to a background QThread that starts after first render. `FoxBrain.capture` waits up to 5s with progress log if model not ready, then falls back (skip indexing this capture, mark for deferred re-index).
- **Files**: [brain/brain.py:L44-L66](file:///c:/Users/rohit/Documents/fox-companion/brain/brain.py#L44-L66)
- **Expected gain**: Startup time <500ms (was 2–8s on first cold run).

#### Step 2.6 — LLM Request Lock Fine-Graining
- **Refactor**: Global `_lock` removed. Instead, use `concurrent.futures.ThreadPoolExecutor(max_workers=2)`: one slot for user-initiated (high priority), one slot for behavior-triggered. If 2 user requests arrive, cancel pending lower-priority using Groq stream + close.
- **Files**: [foxio/fox_brain_llm.py:L30-L76](file:///c:/Users/rohit/Documents/fox-companion/foxio/fox_brain_llm.py#L30-L76)

---

### Phase 3: User Experience Wins (P1)

#### Step 3.1 — Chat History Side Panel
- **New**: `ui/chat_history.py` — `ChatHistory` widget (scrollable QListWidget or custom delegate). `ChatPanel` shows last N user↔fox exchanges. Auto-persists to `chat_history.json` (last 200 entries ring buffer).
- **Integrate**: Click fox (single-click when already visible) → toggles history panel. `ESC` when panel focused hides it. Copy-to-clipboard on item right-click.
- **Files affected**: New file. [main.py:L99-L109](file:///c:/Users/rohit/Documents/fox-companion/main.py#L99-L109) (open_chat), [main.py:L131-L137](file:///c:/Users/rohit/Documents/fox-companion/main.py#L131-L137) (_deliver_reply appends both to bubble + history).
- **Addresses**: U1.

#### Step 3.2 — Bubble Duration Tied to Actual Speech + Text
- **Refactor**: Compute estimated read time as `max(tts_duration, len(text) / 18 chars_per_second)` and use that as the bubble timer instead of hardcoded 15s.
- **Get TTS duration**: For cache hits, store duration alongside wav; for live synthesize, we already have `pcm.num_frames / pcm.sample_rate` from step 2.3.
- **Files**: [main.py:L131-L136](file:///c:/Users/rohit/Documents/fox-companion/main.py#L131-L136), [foxio/voice.py:L49-L55](file:///c:/Users/rohit/Documents/fox-companion/foxio/voice.py#L49-L55)
- **Addresses**: U2.

#### Step 3.3 — VAD-Gated Voice Recording (Auto-Stop)
- **Refactor**: Replace `sd.rec(blocking=True, frames=timeout*SR)` in `_listen_sync` with streaming `sd.InputStream` that uses existing `VoiceActivityDetector` (`vad.py:VoiceActivityDetector`). Endpoint rule: stop after `pause_threshold` seconds of non-speech, or max timeout.
- **Reuse**: VAD class already exists — wire it up.
- **Files**: [foxio/voice_input.py:L81-L141](file:///c:/Users/rohit/Documents/fox-companion/foxio/voice_input.py#L81-L141), [foxio/vad.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/vad.py)
- **Addresses**: U4. Average voice interaction shortened by 3-5 seconds.

#### Step 3.4 — Settings GUI Window
- **New**: `ui/settings_dialog.py` — QDialog with tabbed sections:
  - **Voice**: Voice name dropdown (list from edge-tts list), Volume slider (0-100%), Rate slider, Pitch slider, Test voice button.
  - **Appearance**: Bubble font picker, bubble wrap width, particle density slider, night-mode "Auto/On/Off".
  - **Behavior**: Brain tick interval, speak chance, thresholds (energy/boredom/hunger sliders).
  - **System**: Hotkey rebind input, wake word threshold slider, "Run on Startup" (Windows: registry Run key).
- **Persist**: Read/write into `config.load_settings/save_settings` dict.
- **Files affected**: New file. Integrate into tray menu after the "Voice Mode" separator.
- **Addresses**: U6.

#### Step 3.5 — Multi-Monitor Road & Physics Awareness
- **Refactor**: Query all screens via `QApplication.screens()`. Compute combined virtual desktop geometry. Build a `RoadStrip` per monitor (or one large widget spanning all) — anchor_y per-monitor based on `availableGeometry.bottom()`. Physics bounds use the union of all screens minus gaps.
- **Files**: [main.py:L34-L46](file:///c:/Users/rohit/Documents/fox-companion/main.py#L34-L46), [core/physics.py:L5-L11](file:///c:/Users/rohit/Documents/fox-companion/core/physics.py#L5-L11), [ui/road_strip.py:L1-L32](file:///c:/Users/rohit/Documents/fox-companion/ui/road_strip.py#L1-L32), [core/behavior.py:L60-L66](file:///c:/Users/rohit/Documents/fox-companion/core/behavior.py#L60-L66)
- **Addresses**: U7.

#### Step 3.6 — Activity Categorization Heuristic Upgrade
- **Refactor**: Add process name + executable path detection (Windows: `GetWindowThreadProcessId` + `QueryFullProcessImageName`). Match process name **first**, fall back to title. Add weights: match in both = higher confidence; add categories `gaming` (with trigger lines for U5 coverage), `video`, `reading`.
- **Files**: [foxio/screen_watcher.py:L9-L96](file:///c:/Users/rohit/Documents/fox-companion/foxio/screen_watcher.py#L9-L96)
- **Addresses**: U5. Category accuracy target: >80% (vs current ~55% subjective estimation).

#### Step 3.7 — Memory Viewer / Editor Dialog
- **New**: `ui/memory_viewer.py` — QDialog with a list of facts, filter bar, Delete button, Edit inline. Calls `FoxBrain.retrieve("", top_k=MAX)` on open. Delete → set `valid_to=NOW` in both SQLite and Chroma (soft delete). Edit → old fact superseded, new fact inserted with same entity_key.
- **Trigger**: Tray menu item "Memory Vault..." (below "Talk to Fox").
- **Addresses**: U9.

#### Step 3.8 — Visual Listening Indicator + Onboarding for API Key
- **New UI element**: Mini waveform bar in place of / alongside "Listening..." bubble text — sample RMS from current `_listen_sync` or streaming callback, paint 5 animated bars that scale with volume.
- **New Onboarding step**: If `GROQ_API_KEY` missing on start, show a dialog "Welcome! Fox needs a Groq API key to chat. (1) Get one at console.groq.com (2) Paste below..." with save-to-`.env` helper.
- **Addresses**: U8, R3.

---

### Phase 4: Cross-Platform & Reliability (P2)

#### Step 4.1 — Cross-Platform Audio Playback Abstraction
- **New**: `foxio/audio_backend.py`. Interface: `AudioBackend.play_async(path, on_done)`, `stop()`. Implement three backends:
  - **Windows**: `WinSoundBackend` (existing, upgraded to non-sleep polling for cancellability).
  - **macOS**: `AVFoundation` via pyobjc or fall back to `subprocess` + `afplay`.
  - **Linux**: `miniaudio` (already in deps) or `simpleaudio` / `pyaudio`.
- **Fallback**: If all unavailable, gracefully no-op with log warning.
- **Files**: New file. [foxio/voice.py:L1-L78](file:///c:/Users/rohit/Documents/fox-companion/foxio/voice.py#L1-L78) refactored to use backend.
- **Addresses**: R1, R2.

#### Step 4.2 — Wake Listener Resilience Loop
- **Refactor**: `_run()` outer shell becomes `while self.running: try: ... except: log+backoff_sleep(2)+reinitialize_stream`. After N consecutive failures (10), emit `on_wake_error` callback → tray tooltip "Wake word paused — check mic". Add manual "Restart Wake Listener" tray action.
- **Files**: [foxio/wake_listener.py:L61-L74](file:///c:/Users/rohit/Documents/fox-companion/foxio/wake_listener.py#L61-L74)
- **Addresses**: R4.

#### Step 4.3 — First Run Setup Wizard
- **New**: `ui/first_run_wizard.py`. Pages:
  1. **Welcome** (intro + fox animation preview)
  2. **API Key Setup** with pasted-key validation (make a 1-token Groq test call, "OK" / "Invalid key" feedback)
  3. **Voice picker** (preview 2-3 sample lines per voice)
  4. **Permissions** (global hotkey admin prompt info, mic permission test)
  5. **Done** → onboarding hints start.
- **Gate**: Run only when `settings.json` missing `first_run_done`.
- **Addresses**: R3, indirectly U6.

#### Step 4.4 — Particle Init Cleanup
- **Fix**: Initialize `ParticleManager` size based on screen geometry, not 1920x1080.
- **Files**: [ui/particles.py:L39-L54](file:///c:/Users/rohit/Documents/fox-companion/ui/particles.py#L39-L54)
- **Addresses**: R5.

---

### Phase 5: Architecture Decomposition (P2 — Long Term Health)

#### Step 5.1 — Extract `CompanionApp` Coordinator from `main.py`
- **New class** `core/app.py:CompanionApp`: owns all singletons. Methods:
  - `__init__` → create screen/anchor/physics/road/win/voice/bubble/sprites/behavior/particles/onboarding (moves 80% of main.py top-level code)
  - `setup_tray()` → tray + actions (moves lines L349–L415)
  - `setup_hotkey()` → lazy import keyboard, register
  - `render_loop_body(dt)` → `render_loop()` function body (lines L271-L344)
  - `shutdown()` → Phase 1 Step 1.3's handler
  - Callbacks (`handle_chat_submit`, `_deliver_reply`, `on_wake_detected`, etc.) become methods.
- **Keep `main.py` thin**: ~40 lines: parse args, create QApp, instantiate `CompanionApp`, call `app.run()`, `sys.exit(app.exec())`.
- **Addresses**: T2. Cuts `main.py` complexity ~85%. Enables unit testing of coordination logic.

#### Step 5.2 — Unit Test Harness + Smoke Tests
- **New directory**: `tests/`
- `tests/test_config.py`: atomic settings + corruption recovery
- `tests/test_memory_sql.py`: SQL-injection-resistant FTS search, temporal validity (supersede works, retrieval filters old)
- `tests/test_brain_state.py`: Needs state machine (tired→sit, bored→walk) transitions deterministic with seeded random
- `tests/test_physics.py`: Walk-to-target decelerates within radius, edge-bounce flips direction, drag release applies velocity
- `tests/test_physics.py`: Anchor_y correctly clamps
- Runner: `pytest` (add to requirements.txt dev section or separate `requirements-dev.txt`)
- CI: Optional GitHub Actions workflow `test.yml`.

---

## 4. Milestone Summary Table

| Milestone | Phase | Deliverables | Acceptance Gate |
|-----------|-------|-------------|-----------------|
| **M1: Safe & Stable** | 1 | T1, T2, T3, T4, T5, T6 (6 steps) | Pen-test memory layer with malicious input. Kill process mid-write → restart → no settings loss. Quit <2s, no leftover tmp files. |
| **M2: Fast & Fluid** | 2 | P1–P7 (6 steps) | Startup <1s (warm). Render loop ≥58fps steady on iGPU. Chat-to-speech-bubble <400ms for cached greeting. No UI freeze >50ms during memory capture. |
| **M3: Delightful UX** | 3 | U1–U9 all addressed (8 steps) | Voice queries end ~3s earlier on average. Settings GUI has 10+ configurable items. Multi-monitor users see full-span road. Memory viewer can list and delete facts. |
| **M4: Portable & Trustworthy** | 4 | R1–R5 + cross-platform (4 steps) | Mac/Linux launch: pet animates, TTS audibly plays via backend. Wake listener recovers after 3 simulated USB mic disconnects. First-run wizard completes end-to-end. |
| **M5: Maintainable** | 5 | Architecture + tests (2 steps) | `main.py` <60 lines. ≥20 unit tests, ≥80% code coverage on core modules. Test suite passes in CI in <60s. |

---

## 5. Measurable Success Criteria (Quantifiable KPIs)

### 5.1 Security & Reliability
- **S1**: 0 SQLi findings after running `sqlmap`-style injection test harness with 50 malicious fact payloads against `FoxBrain.capture` / `retrieve`.
- **S2**: Settings corruption test: simulate 100 process kills during `save_settings()` → 0 data loss, 0 invalid JSON.
- **S3**: Clean shutdown: 100 consecutive quit cycles → 0 orphan `tmp_*.mp3/wav` files, 0 lingering processes, 0 audio-device lock on next launch.
- **S4**: Crash rate (unhandled exception log): target <1 per 24h of runtime. Baseline: unknown (currently no counter).

### 5.2 Performance
- **Pm1**: Startup to first frame visible: target <1000ms (warm cache) / <3000ms (cold, first-run with FastEmbed download deferred).
- **Pm2**: Render loop jank: <2 frames >32ms (two frames dropped) per minute of continuous walking + talking. Use `render_loop` timer instrumentation to log dt >16.66ms.
- **Pm3**: End-to-end latency (chat submit → bubble shows reply text): median <1200ms (baseline currently ~2000-4000ms, subjective).
- **Pm4**: TTS cache hit ratio for repeated lines: ≥40% after 30 minutes of typical usage.
- **Pm5**: Private bytes (memory usage): <600MB idle, <900MB peak (from ~700MB baseline).

### 5.3 User Experience
- **Ux1**: Voice interaction total time (wake → fox reply starts) 50th percentile: <5s (was ~8s baseline, 3s timeout waste removed).
- **Ux2**: Settings changes applied without restart: 100% of options in Settings GUI.
- **Ux3**: Memory viewer fact edit → reflected in next retrieval: 100% consistency (10 trials, no crosstalk).
- **Ux4**: Activity classifier accuracy on 20 labeled window-title+process test cases: ≥85%.
- **Ux5**: Multi-monitor: fox can walk from left edge of leftmost screen to right edge of rightmost without boundary errors (10 passes, 0 stuck events).

### 5.4 Maintainability
- **M1**: `main.py` line count: <60 lines (425 → 60, -86% reduction).
- **M2**: McCabe cyclomatic complexity top-3 functions <15 each (current `render_loop`, `choose_state`, `FoxBrain.capture` are all >20 estimated).
- **M3**: Unit test coverage of core modules (`core/*`, `brain/*`): ≥70% statement coverage.
- **M4**: Platform porting. Success = application starts on each of: Windows 11, macOS 13+, Ubuntu 22.04.

---

## 6. Potential Dependencies & Considerations

### 6.1 New Dependencies (Minimize)
| Dependency | Purpose | Justification | Required |
|------------|---------|---------------|----------|
| `pytest` | Test runner | M5 | Dev-only (requirements-dev.txt) |
| `pytest-cov` | Coverage metrics | M5, M3 | Dev-only |
| `pyobjc` (macOS) | Audio playback | M4, Step 4.1 | Optional — try/except import |
| `simpleaudio` (Linux fallback) | Audio backend | M4 | Optional — `miniaudio` preferred (already in deps) |

### 6.2 No New Dependencies — Prefer Existing Assets
- `miniaudio` is already in `requirements.txt` → use it for cross-platform audio backend rather than adding `simpleaudio`/`pygame.mixer`.
- `PyQt6` tools: use `QThread`, `QThreadPool`, `QSettings` (or keep json), `QGraphicsDropShadowEffect`, `QStandardPaths` (for cache dirs) — all already available.
- ChromaDB/FastEmbed/Groq remain untouched.

---

## 7. Risk Handling & Rollback Strategy

| Risk | Likelihood | Impact | Mitigation | Rollback |
|------|-----------|--------|------------|----------|
| **SQL parameterization breaks existing queries** | Medium | High (memory retrieval broken) | Write `tests/test_memory_sql.py` BEFORE refactor, with golden fixtures — run after. | Revert to old `_fts_search` if queries fail. |
| **Async memory captures introduce race conditions on SQLite** | Medium | High | Use single DB-worker-thread-with-queue pattern. Never call SQLite from multiple threads. | Fallback to synchronous capture behind a loading spinner. |
| **TTS cache grows unbounded on disk** | Low | Medium | LRU with size cap (50MB) + age (7 days) enforced on app start. Simple `os.path.getsize` loop. | Delete `%TEMP%/fox-tts-cache/`; user can clear via new settings GUI. |
| **Phase 5 App class introduces subtle regressions** | High (any large refactor) | Medium | Do Phase 5 LAST — after M1–M4 stabilize. Use feature flags: environment variable `FOX_USE_NEW_APP=1` to toggle coordinator. | Delete the env var. Both code paths can live for 1 release. |
| **Cross-platform audio backends flaky** | Medium | Medium | Graceful degradation: backend fails → fall back to no-op with prominent tray warning. Never crash the app. | Remove audio backend abstraction; restore winsound on Windows only. |
| **First-run wizard scope creep / too long** | Medium | Low | Max 4 pages. User can skip all and enter default + manual API key paste later. Timer: limit user-facing prompts to <60 seconds of work (copy-paste API key, click test, done). | User can bypass by closing wizard. Functionality degrades gracefully (same current behavior). |

---

## 8. Proposed Execution Order (Dependency Graph)

```
Phase 1 (Safety first — MUST ship together):
 ├─ T1 (SQL fix)         [no deps]
 ├─ T2 (Atomic settings) [no deps]
 ├─ T5 (datetime cleanup)[no deps]
 ├─ T3 (Shutdown)        [after T2, T5 to avoid settings race]
 └─ T4 + T6 (Misc)       [no deps]

Phase 2 (Performance — can interleave with Phase 3):
 ├─ P1 Sprite cache    [no deps]
 ├─ P4 Shadow opt      [no deps]
 ├─ P7 FastEmbed lazy  [no deps]
 ├─ P6 TTS cache       [after P7? No — independent]
 ├─ P2 Memory async    [after T1 to re-use safe SQL]
 └─ P5 LLM fine-grain  [no deps]

Phase 3 (UX — order matters for user-facing value):
 ├─ U4 (VAD-gated rec)  [reuses existing VAD: early win]
 ├─ U3 (Stop speaking)  [after P6 TTS cache installs real stop backend]
 ├─ U2 (Duration sync)  [after P6]
 ├─ U6 (Settings GUI)   [foundational for U5 adjustments]
 ├─ U1 (Chat history)   [after coordinator skeleton if possible]
 ├─ U8 (Listening + key)|parallel|
 ├─ U7 (Multi-monitor)  [needs physics refactor + road]
 ├─ U5 (Activity class) [needs process path helper]
 └─ U9 (Memory viewer)  [after T1 safe SQL + P2 async settled]

Phase 4 (Portability):
 ├─ R3 (API wizard)    [best after U6 settings UI scaffolding exists]
 ├─ R4 (Wake resilient)[no deps]
 ├─ R1+R2 (Audio back) [after P6, Step 2.3 redesign]
 └─ R5 (Particles)     [no deps]

Phase 5 (Long-horizon cleanup):
 └─ M5-1 App class + M5-2 tests
```

Interleaving recommendation to maximize value delivery:
1. Ship M1 (Phase 1) = 1 release on its own — safety wins don't need waiting.
2. Release M2 (Phase 2) + M3 steps U4, U3, U2, U6 next.
3. Release the rest of M3 + M4.
4. M5 cleanup can happen continuously in parallel during later phases.

---

## 9. File Reference Index (Clickable)

For traceability, here are the canonical entry points referenced throughout:

| File | Lines | Role |
|------|-------|------|
| [main.py](file:///c:/Users/rohit/Documents/fox-companion/main.py) | 1–425 | App entry, render loop, tray, wiring |
| [core/config.py](file:///c:/Users/rohit/Documents/fox-companion/core/config.py) | 1–209 | Constants + settings I/O |
| [core/brain.py](file:///c:/Users/rohit/Documents/fox-companion/core/brain.py) | 1–84 | Needs-based AI state machine |
| [core/behavior.py](file:///c:/Users/rohit/Documents/fox-companion/core/behavior.py) | 1–179 | Behavior FSM + speech triggers |
| [brain/brain.py](file:///c:/Users/rohit/Documents/fox-companion/brain/brain.py) | 1–572 | Long-term memory (Chroma + SQLite FTS) |
| [foxio/voice.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/voice.py) | 1–78 | TTS engine |
| [foxio/voice_input.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/voice_input.py) | 1–154 | STT recorder |
| [foxio/wake_listener.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/wake_listener.py) | 1–102 | Wake-word detection |
| [foxio/screen_watcher.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/screen_watcher.py) | 1–115 | Activity/idle detection |
| [foxio/fox_brain_llm.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/fox_brain_llm.py) | 1–76 | Groq LLM gateway |
| [foxio/vad.py](file:///c:/Users/rohit/Documents/fox-companion/foxio/vad.py) | 1–82 | Voice activity detector |
| [ui/window.py](file:///c:/Users/rohit/Documents/fox-companion/ui/window.py) | 1–209 | Companion widget (drag, click, menu) |
| [ui/sprite_manager.py](file:///c:/Users/rohit/Documents/fox-companion/ui/sprite_manager.py) | 1–48 | Animation player |
| [ui/speech_bubble.py](file:///c:/Users/rohit/Documents/fox-companion/ui/speech_bubble.py) | 1–206 | Floating dialogue |
| [ui/chat_input.py](file:///c:/Users/rohit/Documents/fox-companion/ui/chat_input.py) | 1–79 | Text chat box |
| [ui/particles.py](file:///c:/Users/rohit/Documents/fox-companion/ui/particles.py) | 1–162 | Particle effects overlay |
| [ui/onboarding.py](file:///c:/Users/rohit/Documents/fox-companion/ui/onboarding.py) | 1–97 | First-time hint flow |
| [ui/road_strip.py](file:///c:/Users/rohit/Documents/fox-companion/ui/road_strip.py) | 1–32 | Tiled road strip |
| [core/physics.py](file:///c:/Users/rohit/Documents/fox-companion/core/physics.py) | 1–120 | 2D kinematics |
| [core/dialogue.py](file:///c:/Users/rohit/Documents/fox-companion/core/dialogue.py) | 1–43 | Dialogue line DB |
| [core/logger.py](file:///c:/Users/rohit/Documents/fox-companion/core/logger.py) | 1–27 | Logging infra |
| [core/easing.py](file:///c:/Users/rohit/Documents/fox-companion/core/easing.py) | 1–4 | Breathing curve |
| [requirements.txt](file:///c:/Users/rohit/Documents/fox-companion/requirements.txt) | 1–17 | Declared dependencies |

**— End of Plan —**

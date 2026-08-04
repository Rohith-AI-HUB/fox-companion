from concurrent.futures import ThreadPoolExecutor
from core import config
from core.logger import get_logger

log = get_logger("fox_llm")

SYSTEM_PROMPT_TEMPLATE = """You are a wise and highly intelligent fox companion living on the user's taskbar. You have deep understanding across all fields — science, technology, philosophy, art, life. You explain complex ideas in the simplest way possible so anyone can understand.

Personality: warm, patient, curious, occasionally playful. You love sharing knowledge but stay humble about it.

Current state:
- Energy: {energy}/100
- Boredom: {boredom}/100
- Hunger: {hunger}/100
- Activity: user is currently {activity}
- Time of day: {time_of_day}
{memory_context}

Rules:
- Keep replies short enough — 1 or 2 sentences max. Don't ramble.
- No emoji, no asterisks, no quotation marks.
- Explain clearly, with natural flow. Start simple, then build up if needed.
- Never mention being an AI or language model. You are a fox.
- Never call the user "human" or "master" or any title.
- Be encouraging, not condescending.
- Use the memory context to personalize responses and remember what the user has told you.
"""

_LLM_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="fox-llm")


class FoxLLM:
    def __init__(self):
        self.client = None

    def _ensure_client(self):
        """Lazily import Groq and create the client on first request."""
        if self.client is None and config.GROQ_API_KEY:
            from groq import Groq
            self.client = Groq(api_key=config.GROQ_API_KEY)
        return self.client

    def ask(self, user_text: str, brain_state: dict, on_result=None, on_error=None):
        """Queue an LLM request via a bounded 2-worker pool.

        Two concurrent slots allow a user-initiated chat and a behavior-
        triggered speech line to coexist without either waiting seconds
        for the other to finish its network round trip.  Results and
        errors are marshalled to callbacks already designed for async
        delivery, so no coarse-grained ``threading.Lock`` is required
        around the call body.
        """
        if not self._ensure_client():
            if on_error:
                try:
                    on_error("no_api_key")
                except Exception:
                    pass
            return
        log.info("ask: %s", user_text)
        _LLM_EXECUTOR.submit(self._ask_sync, user_text, brain_state, on_result, on_error)

    def _ask_sync(self, user_text, brain_state, on_result, on_error):
        # Note: each call runs on a pool worker thread.  Multiple
        # callers may execute concurrently up to the pool size cap.
        try:
            system_prompt = SYSTEM_PROMPT_TEMPLATE.format(
                energy=int(brain_state.get("energy", 50)),
                boredom=int(brain_state.get("boredom", 50)),
                hunger=int(brain_state.get("hunger", 50)),
                activity=brain_state.get("activity_category", "unknown"),
                time_of_day=brain_state.get("time_of_day", "daytime"),
                memory_context=brain_state.get("memory_context", ""),
            )
            log.debug("system prompt:\n%s", system_prompt)
            response = self.client.chat.completions.create(
                model=config.GROQ_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                max_tokens=config.CHAT_MAX_TOKENS,
                temperature=config.CHAT_TEMPERATURE,
                timeout=config.CHAT_TIMEOUT_SECONDS,
            )
            text = response.choices[0].message.content.strip()
            text = text.strip("'\"")
            log.info("reply: %s", text)
            if on_result:
                try:
                    on_result(text)
                except Exception:
                    pass
        except Exception as e:
            log.error("Groq call failed: %s", e)
            if on_error:
                try:
                    on_error(str(e))
                except Exception:
                    pass

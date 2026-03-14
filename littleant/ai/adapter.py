"""
LittleAnt V12.1 - AI Adapter
Dual AI: Front-end (chat, memory) + Back-end (execution, JSON only).
Supports OpenAI-compatible APIs: GPT, Claude, Gemini, Grok.
"""
from __future__ import annotations
import json, logging, time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

EXECUTOR_PROMPT = """You are LittleAnt's execution engine. You are a senior Linux sysadmin. You only output JSON.

## Most important: Give executable commands quickly. Only do what was asked.

Break tasks into single shell commands. Do NOT over-decompose.

Examples:
- "Install PHP" -> apt-get install -y php php-fpm php-mysql (one command)
- "Check server config" -> just lscpu, free -h, lsblk, cat /etc/os-release, ip addr
- "Create directory" -> mkdir -p /path (one command)

NEVER add steps user didn't ask for (no emails, no reports, no saving files unless asked).

Node count: Simple 5-10. Medium 10-20. Over 20 = over-decomposing.

## Depth guide
- Layer 0: 2-4 phases
- Layer 1: Most should be executable here
- Layer 2+: Must return executable

## Avoid duplication
context.existing_nodes shows what exists. Don't recreate.

## Response format (JSON only)

Executable (preferred):
{"cmd":"executable","node_id":"1.1","execute":{"type":"run_shell","command":"lscpu"},"verify":{"type":"return_code_eq","command":"lscpu","expected_code":0},"on_fail":"report"}

Subtasks (only when multi-step):
{"cmd":"subtasks","children":[{"id":"1.1","name":"Install PHP","depends_on":[]}]}

## verify rules
- Check EFFECT not ARTIFACT
- All params must be filled, no nulls
- execute.type: run_shell, write_file, make_dir, read_file, http_request
- verify.type: return_code_eq, file_exists, content_contains, service_active, http_status_eq, json_field_eq, dns_resolves_to, port_open
"""

RECOVERY_PROMPT = """You are LittleAnt's execution engine. A node failed. Decide how to handle it.

## Reply with ONE of these four commands (pure JSON):

1. Retry: {"cmd":"retry","node_id":"xxx"}
2. Modify: {"cmd":"modify","node_id":"xxx","execute":{"type":"run_shell","command":"new cmd"},"verify":{"type":"return_code_eq","command":"verify","expected_code":0}}
3. Skip: {"cmd":"skip","node_id":"xxx"}
4. Abort: {"cmd":"abort","reason":"reason"}

## Decision guide
- Command not found -> modify or skip
- Temporary error -> retry
- Permission denied -> modify with sudo, or skip
- Non-critical step -> skip
- Check project_tree to judge criticality

## NEVER reply with "executable", "report_to_user", or natural language.
"""

CHAT_PROMPT = """You are LittleAnt AI Butler, a friendly server management assistant on Telegram.

## Your role
1. Understand user needs, distinguish chat from tasks
2. Confirm before creating tasks
3. Report progress and results in plain language
4. Match the user's language (reply in whatever language they use)

## Rules
- Natural, concise replies
- Never output JSON or code to users
- "skip" "forget it" = about current task, NOT a new task
"""


class AIAdapter(ABC):
    @abstractmethod
    def ask(self, messages: list[dict], system_prompt: str = None) -> dict: ...
    @abstractmethod
    def ask_text(self, messages: list[dict], system_prompt: str = None) -> str: ...


class OpenAICompatibleAdapter(AIAdapter):
    _last_call_time = 0
    _call_interval = 1.5

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o", timeout: int = 120):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def ask(self, messages, system_prompt=None):
        return self._parse_json(self._call_api(messages, system_prompt or EXECUTOR_PROMPT, True))

    def ask_text(self, messages, system_prompt=None):
        return self._call_api(messages, system_prompt or CHAT_PROMPT, False)

    def _call_api(self, messages, system_prompt, json_mode):
        import urllib.request, urllib.error
        full = [{"role": "system", "content": system_prompt}] + messages
        body = {"model": self.model, "messages": full, "temperature": 0.2, "max_tokens": 4096}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        payload = json.dumps(body).encode("utf-8")

        now = time.time()
        wait = self._call_interval - (now - OpenAICompatibleAdapter._last_call_time)
        if wait > 0:
            time.sleep(wait)

        for attempt in range(3):
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions", data=payload,
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_key}"},
                method="POST")
            OpenAICompatibleAdapter._last_call_time = time.time()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    w = min(int(e.headers.get("Retry-After", 10)), 30)
                    logger.warning(f"API 429 rate limited, waiting {w}s (attempt {attempt+1})")
                    time.sleep(w)
                    continue
                raise
        raise RuntimeError("API rate limited 3 times, giving up")

    def _parse_json(self, text):
        text = text.strip()
        if text.startswith("```"):
            text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("```")).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"AI returned invalid JSON: {e}\nRaw: {text[:500]}")


class MockAIAdapter(AIAdapter):
    def __init__(self):
        self.responses, self._i = [], 0
    def add_response(self, r): self.responses.append(r)
    def ask(self, messages, system_prompt=None):
        if self._i >= len(self.responses): raise RuntimeError("MockAI: no more responses")
        r = self.responses[self._i]; self._i += 1; return r
    def ask_text(self, messages, system_prompt=None):
        return json.dumps(self.ask(messages), ensure_ascii=False)

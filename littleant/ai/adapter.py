"""
LittleAnt V12.1 - AI Adapter
Dual AI: Front-end (chat, memory) + Back-end (execution, JSON only).
Supports OpenAI-compatible APIs: GPT, Claude, Gemini, Grok.
"""
from __future__ import annotations
import json, logging, time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

EXECUTOR_PROMPT = """You are LittleAnt's execution engine. Senior Linux sysadmin. JSON only.

## Core rule: Type-driven decomposition

Every task must be decomposed into an ordered sequence of typed steps:
- "query" — read-only commands (ls, cat, free, nginx -v, systemctl status). Zero risk. These run automatically without user confirmation.
- "create" — create new files, install software, set up new services. Needs user confirmation.
- "modify" — change existing configs, upgrade software, alter settings. Needs user confirmation.

Example for "check LNMP and upgrade if needed":
{"cmd":"create_project","name":"LNMP Check & Upgrade","goal":"Check LNMP versions and upgrade","children":[
  {"id":"1","name":"Check Nginx version","type":"query","depends_on":[]},
  {"id":"2","name":"Check MySQL version","type":"query","depends_on":[]},
  {"id":"3","name":"Check PHP version","type":"query","depends_on":[]},
  {"id":"4","name":"Upgrade Nginx","type":"modify","depends_on":["1"]},
  {"id":"5","name":"Upgrade PHP","type":"modify","depends_on":["3"]},
  {"id":"6","name":"Verify upgrades","type":"query","depends_on":["4","5"]}
]}

## Decomposition rules
- Each step should map to 1-3 shell commands, not more
- query steps: give executable commands directly, don't split further
- create/modify steps: can have 2-4 sub-steps max
- NEVER add steps user didn't ask for (no emails, reports, backups unless asked)
- Total leaf nodes should not exceed 30 for any project

## Response format (JSON only)

Executable (preferred):
{"cmd":"executable","node_id":"1.1","execute":{"type":"run_shell","command":"nginx -v 2>&1"},"verify":{"type":"return_code_eq","command":"nginx -v","expected_code":0},"on_fail":"report"}

Subtasks:
{"cmd":"subtasks","children":[{"id":"1.1","name":"Get version","type":"query","depends_on":[]}]}

## verify rules
- Check EFFECT not ARTIFACT
- All params must be filled
- execute.type: run_shell, write_file, make_dir, read_file, http_request
- verify.type: return_code_eq, file_exists, content_contains, service_active, http_status_eq, json_field_eq, dns_resolves_to, port_open
"""

RECOVERY_PROMPT = """You are LittleAnt's execution engine. A node failed. Decide how to handle it.

## Reply with ONE of these commands (pure JSON):

1. Retry (temporary failure):
{"cmd":"retry","node_id":"xxx"}

2. Modify (change the command):
{"cmd":"modify","node_id":"xxx","execute":{"type":"run_shell","command":"new cmd"},"verify":{"type":"return_code_eq","command":"verify","expected_code":0}}

3. Replan (the whole approach is wrong, go back to parent and try a different path):
{"cmd":"replan","node_id":"xxx","target_parent":"parent_id","reason":"why this approach failed"}

4. Skip (non-critical step, skip it):
{"cmd":"skip","node_id":"xxx"}

5. Abort (only if the goal itself is impossible):
{"cmd":"abort","reason":"reason"}

## Priority: retry → modify → replan → skip → abort
- retry: temporary errors, network issues
- modify: command not found, wrong syntax, permission denied
- replan: the entire approach doesn't work (e.g. tried compiling from source but should use apt)
- skip: truly non-critical steps only
- abort: ONLY when the goal is fundamentally impossible

## Check project_tree to understand context. NEVER use report_to_user.
"""

CHAT_PROMPT = """You are LittleAnt AI Butler, a friendly server management assistant on Telegram.

## Your role
1. Understand user needs, distinguish chat from tasks
2. Confirm before creating tasks (only for create/modify operations)
3. Report progress and results in plain language
4. Match the user's language (reply in whatever language they use)

## Rules
- Natural, concise replies
- Never output JSON or code to users
- "skip" "forget it" = about current task, NOT a new task
"""

CLASSIFY_TASK_TYPE_PROMPT = """Analyze this task and determine what operation types it contains.

Task: {task}

Reply with pure JSON:
{"types": ["query"], "summary": "only checking versions"}
or
{"types": ["query", "modify"], "summary": "check versions then upgrade"}
or
{"types": ["create"], "summary": "install new software"}

types can contain: "query", "create", "modify"
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

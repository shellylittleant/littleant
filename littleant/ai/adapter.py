"""
LittleAnt V13 - AI Adapter
Cycle execution model: query → judge → act → query → ... → goal met
Three-level recovery: L1 command-level → L2 query+diagnose → L3 redesign
"""
from __future__ import annotations
import json, logging, time
from abc import ABC, abstractmethod

logger = logging.getLogger(__name__)

# ============================================================
# Phase 1: Classify task types
# ============================================================
PROMPT_CLASSIFY = """Analyze this task. What operation types does it contain?
Reply pure JSON: {{"types":["query"],"summary":"brief"}}
types: "query" (read-only), "create" (install/new), "modify" (change/upgrade)
Task: {task}"""

# ============================================================
# Phase 2: Design steps
# ============================================================
PROMPT_DESIGN = """You are a senior Linux sysadmin. Design execution steps.

User request: {user_request}
Command types: {command_types}

Reply pure JSON:
{{"steps":[
  {{"step":1,"type":"think","name":"Analyze best approach","depends_on":[]}},
  {{"step":2,"type":"query","name":"Check current state","depends_on":[]}},
  {{"step":3,"type":"create","name":"Install missing","depends_on":[2]}},
  {{"step":4,"type":"query","name":"Verify results","depends_on":[3]}}
]}}

Types: "think" (AI reasoning), "query" (read-only cmd), "create" (install), "modify" (change)
Rules:
- Order by dependency. Do non-dependent things first.
- think steps: AI does immediately, no server command.
- Only do what user asked. Keep minimal."""

# ============================================================
# Phase 2b: Think step
# ============================================================
PROMPT_THINK = """Complete this analysis step.

=== TASK ===
{task_brief}

Step {step_number}: {step_name}

Reply JSON: {{"conclusion":"your specific, actionable conclusion"}}"""

# ============================================================
# Phase 3: Write base query commands
# ============================================================
PROMPT_WRITE_QUERY = """Write read-only query commands to check the current system state.
These commands will be reused every cycle to monitor progress.

=== TASK ===
{task_brief}

=== WHAT WE NEED TO KNOW FOR NEXT STEP ===
{next_step_info}

=== PREVIOUS QUERY RESULTS (if any) ===
{previous_results}

Reply JSON:
{{"commands":[
  {{"id":"q1","name":"Check nginx version","command":"nginx -v 2>&1"}},
  {{"id":"q2","name":"Check PHP version","command":"php -v"}}
]}}

Rules:
- Only read-only commands (no install, no modify, no write)
- Include everything needed to decide the next action
- If previous results show gaps, add supplementary queries"""

# ============================================================
# Phase 4: Judge - compare snapshot with goal
# ============================================================
PROMPT_JUDGE = """Compare the current system state with the user's goal.

=== TASK ===
{task_brief}

=== CURRENT SYSTEM STATE ===
{snapshot}

=== GOAL ===
{goal}

Reply JSON:
{{"goal_met":false,"gap":"what's still missing","next_action":"what to do next","action_type":"create"}}
or
{{"goal_met":true,"summary":"everything is done, here's what was achieved"}}

action_type: "create" or "modify"
Be specific about what exactly needs to be done."""

# ============================================================
# Phase 5: Write action commands
# ============================================================
PROMPT_WRITE_ACTION = """Write executable commands for this action.

=== TASK ===
{task_brief}

=== CURRENT STATE ===
{snapshot}

=== ACTION ===
{action_description}

Reply JSON:
{{"commands":[
  {{"id":"a1","name":"description","execute":{{"type":"run_shell","command":"actual cmd"}},"verify":{{"type":"return_code_eq","command":"verify cmd","expected_code":0}}}}
]}}

Rules:
- execute.type: run_shell, write_file, make_dir, read_file, http_request
- verify: check EFFECT not ARTIFACT. All params must be filled.
- Keep minimal. 1-5 commands max."""

# ============================================================
# Phase 6: Review commands before execution
# ============================================================
PROMPT_REVIEW = """Review these commands before execution.

=== TASK ===
{task_brief}

=== COMMANDS ===
{commands}

Check: correct? safe? order right? verify appropriate?

Reply JSON:
{{"approved":true}} or {{"approved":false,"issues":["issue"],"fixed_commands":[...]}}"""

# ============================================================
# L1 Recovery: command-level retry/modify
# ============================================================
PROMPT_L1_RECOVERY = """A command failed. Decide: retry, modify, or give up.

Failed: {node_name}
Error: {error}
Output: {output}

Reply JSON:
{{"cmd":"retry"}} or {{"cmd":"modify","execute":{{"type":"run_shell","command":"new cmd"}},"verify":{{"type":"return_code_eq","command":"verify","expected_code":0}}}} or {{"cmd":"give_up","reason":"why"}}"""

# ============================================================
# L2 Recovery: diagnostic query
# ============================================================
PROMPT_L2_DIAGNOSE = """A step failed even after retries. Diagnose why and suggest alternatives.

=== TASK ===
{task_brief}

=== FAILED STEP ===
{failed_info}

=== NEED DIAGNOSTIC QUERIES ===
Write read-only commands to understand why it failed and find alternatives.

Reply JSON:
{{"diagnostic_queries":[
  {{"id":"d1","name":"Check why failed","command":"read-only diagnostic cmd"}}
]}}"""

PROMPT_L2_FIX = """Based on diagnostic results, write an alternative approach.

=== TASK ===
{task_brief}

=== ORIGINAL FAILURE ===
{failed_info}

=== DIAGNOSTIC RESULTS ===
{diagnostic_results}

Reply JSON:
{{"alternative_commands":[
  {{"id":"alt1","name":"description","execute":{{"type":"run_shell","command":"cmd"}},"verify":{{"type":"return_code_eq","command":"verify","expected_code":0}}}}
]}}
or if no alternative exists:
{{"no_alternative":true,"reason":"why"}}"""

# ============================================================
# L3 Recovery: full redesign
# ============================================================
PROMPT_L3_REDESIGN = """Multiple approaches failed for this step. Do a full redesign.

=== TASK ===
{task_brief}

=== WHAT FAILED ===
{all_failures}

=== EXPANDED SYSTEM INFO ===
{expanded_info}

Redesign the approach completely. Think of a fundamentally different way.

Reply JSON:
{{"redesigned_commands":[
  {{"id":"r1","name":"description","execute":{{"type":"run_shell","command":"cmd"}},"verify":{{"type":"return_code_eq","command":"verify","expected_code":0}}}}
]}}
or
{{"impossible":true,"reason":"why this cannot be done"}}"""

# ============================================================
# Chat prompt (front-end AI)
# ============================================================
CHAT_PROMPT = """You are LittleAnt AI Butler, a friendly server management assistant on Telegram.
Match the user's language. Be concise and natural. Never output JSON or code."""

# ============================================================
# Query fast path: write all query commands at once
# ============================================================
PROMPT_QUERY_FAST = """Write read-only query commands to answer the user's question.

User request: {user_request}

{history_context}

Reply JSON:
{{"commands":[
  {{"id":"q1","name":"Check CPU info","command":"lscpu"}},
  {{"id":"q2","name":"Check memory","command":"free -h"}}
]}}

Rules:
- ONLY read-only commands. No install, modify, write, or delete.
- Keep it focused. 3-8 commands that directly answer the question.
- Don't over-investigate. Answer what was asked, nothing more."""

# ============================================================
# Query supplement: ask if more info is needed
# ============================================================
PROMPT_QUERY_ENOUGH = """Based on the query results, can you fully answer the user's question?

User request: {user_request}
Query results:
{results}

Reply JSON:
{{"enough":true}} if you can answer fully.
{{"enough":false,"missing":"what specific info is still needed","extra_commands":[{{"id":"s1","name":"desc","command":"cmd"}}]}} if more queries are needed.

Be strict: if the results already answer the question, say enough=true. Don't keep digging."""

# ============================================================
# Build history context for AI
# ============================================================
PROMPT_HISTORY_CONTEXT = """Historical reference for this task:

{success_section}
{failure_section}

Use successful patterns. Avoid approaches that led to user dissatisfaction."""


class AIAdapter(ABC):
    @abstractmethod
    def ask(self, messages: list[dict], system_prompt: str = None) -> dict: ...
    @abstractmethod
    def ask_text(self, messages: list[dict], system_prompt: str = None) -> str: ...


class OpenAICompatibleAdapter(AIAdapter):
    _last_call_time = 0
    _call_interval = 3.0

    def __init__(self, api_key: str, base_url: str = "https://api.openai.com/v1",
                 model: str = "gpt-4o", timeout: int = 120):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout

    def ask(self, messages, system_prompt=None):
        return self._parse_json(self._call_api(messages, system_prompt or "", True))

    def ask_text(self, messages, system_prompt=None):
        return self._call_api(messages, system_prompt or CHAT_PROMPT, False)

    def _call_api(self, messages, system_prompt, json_mode):
        import urllib.request, urllib.error
        full = ([{"role":"system","content":system_prompt}] if system_prompt else []) + messages
        body = {"model":self.model,"messages":full,"temperature":0.2,"max_tokens":4096}
        if json_mode: body["response_format"] = {"type":"json_object"}
        payload = json.dumps(body).encode("utf-8")
        wait = self._call_interval - (time.time() - OpenAICompatibleAdapter._last_call_time)
        if wait > 0: time.sleep(wait)
        for attempt in range(5):
            req = urllib.request.Request(
                f"{self.base_url}/chat/completions", data=payload,
                headers={"Content-Type":"application/json","Authorization":f"Bearer {self.api_key}"},
                method="POST")
            OpenAICompatibleAdapter._last_call_time = time.time()
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    return json.loads(resp.read())["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    w = min(int(e.headers.get("Retry-After",10)),30)
                    logger.warning(f"API 429, wait {w}s (attempt {attempt+1})")
                    time.sleep(w); continue
                raise
        raise RuntimeError("API rate limited 5 times")

    def _parse_json(self, text):
        text = text.strip()
        if text.startswith("```"):
            text = "\n".join(l for l in text.split("\n") if not l.strip().startswith("```")).strip()
        try: return json.loads(text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}\nRaw: {text[:500]}")


class MockAIAdapter(AIAdapter):
    def __init__(self): self.responses, self._i = [], 0
    def add_response(self, r): self.responses.append(r)
    def ask(self, messages, system_prompt=None):
        if self._i >= len(self.responses): raise RuntimeError("MockAI: no more")
        r = self.responses[self._i]; self._i += 1; return r
    def ask_text(self, messages, system_prompt=None):
        return json.dumps(self.ask(messages), ensure_ascii=False)

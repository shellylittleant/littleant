# Changelog

## V14.1 — 2026-06-12 (evaluation fixes)

Security and measurement-integrity fixes from the code audit. No architectural change.

### Security
- **Access control (was missing entirely)**: the bot now enforces an admin allowlist.
  First contact auto-registers the sole admin; everyone else is denied until added via
  `/addadmin <chat_id>`. Guards added to every message, callback, and command entry point.
  Admins persist in `config.json` under `admin_chat_ids`.
- **Read-only sandbox hardening**: removed interpreters (`python`/`python3`/`php`/`node`),
  `awk`, and `find` from the read-only whitelist — they can execute arbitrary code or write
  files, which a substring blacklist cannot contain.

### Measurement integrity (experiment log)
- **Token usage is now recorded**: both adapters parse `usage` from the API response and
  every AI call logs `api_tokens_in`/`api_tokens_out` (previously always 0).
- **Accurate per-task AI call count**: `api_calls_total` is now derived from the black box
  (`count_ai_calls`, counting `ai_*` events) instead of the legacy in-memory counter that
  was never incremented on the V14 paths (previously 0 for every real run).
- **Linear-mode task success is now mechanical**: added a final verification gate that
  re-checks every created file (exists & non-empty) and re-runs every post-command verify
  (return code 0). Tasks now return `completed`/`failed` based on real effects rather than
  unconditionally reporting success.
- **Verifiers are fail-closed**: an under-specified verify spec now fails (and enters
  recovery) instead of silently passing.

### Correctness
- **Native Anthropic adapter**: Claude now uses the real Messages API (`/v1/messages`,
  `x-api-key`, top-level `system`, native image blocks) instead of the OpenAI-compatible
  path that 404s against `api.anthropic.com`. Provider switching rebuilds the adapter so
  cross-protocol switches work.
- **Template library gating**: only mechanically-verified successes are saved; query tasks
  and failed runs no longer pollute the library (protects the evolution-protocol experiment).

### Engineering
- SQLite connections use WAL + `busy_timeout=30s` to avoid dropped events under concurrency.
- `MAX_CYCLES` / `MAX_REVIEW_ROUNDS` moved from hardcoded constants into `config.py`.

---

## V14 — 2026-03-15

### Dual Execution Architecture (V13 query + V14 linear)
- **Query tasks** use V13 cycle model: query → judge → supplement → report
- **Create/modify tasks** use V14 linear model: scan → plan → batch files → execute → verify
- System scan (V13) is always the first step for any task — AI sees real environment before acting

### Batch File Generation
- File content no longer passes through JSON (solves JSON parsing failures with HTML/CSS)
- All files generated in one API call using ===FILE: path=== / ===END_FILE=== format
- Program writes files directly via Python open().write() — no shell heredoc, no escaping issues
- Fallback: if batch parse fails, generates files one at a time

### Judge Scope Control
- AI cannot add requirements the user didn't ask for (no unsolicited SSL, DNS, firewall, etc.)
- "Perfection is the enemy of done" — core deliverables complete = task complete

### One-Time Authorization
- User authorizes task once at creation, no repeated confirmations during execution
- 4 buttons: [✅ Confirm] [⚡ Auto-execute] [✏️ Modify task] [❌ Cancel]
- Auto-execute: fully silent, report at end
- Risk assessment shown upfront (medium for create/modify, high for delete operations)

### User Feedback System
- After task completion: [✅ Satisfied] [❌ Not satisfied]
- Unsatisfied → prompt for specific reason, stored in DB
- History search before each task: successful patterns as reference, failed patterns as warnings

### Experiment Log (Black Box)
- 28-column experiment_log table records every interaction
- 31 event types covering: user messages, AI prompts/responses, commands, stdout/stderr, verify results, recovery decisions
- All data exportable via SQL for paper/research

### Other
- Query fast path: pure query tasks complete in 2-4 API calls
- Three-level recovery shared across both execution modes

---

## V13 — 2026-03-15

### Cycle Execution Model
- query → judge → act → query → ... → goal met
- Dynamic query commands, Task Brief, type-driven classification
- Three-level recovery: L1 command → L2 diagnose → L3 redesign
- DeepSeek support, AI model hot-switching

---

## V12.1 — 2026-03-14

### Dual AI Architecture
- Front-end AI (chat, read-only) + Back-end AI (execution, read-write)
- Recursive decomposition, mechanical verification, effect principle
- Multi-provider AI support, multi-language, zero dependencies

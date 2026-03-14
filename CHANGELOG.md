# Changelog

## V13 — 2026-03-15

### Cycle Execution Model (architecture change)
- Replaced recursive decomposition with **cycle-based execution**: query → judge → act → query → ... → goal met
- Each cycle: scan system state → AI compares with goal → AI decides action → execute → rescan
- No more one-shot planning. Every action is based on real, current system data
- Dynamic query commands: AI adjusts what to check each cycle based on what the next action needs

### Task Brief (project specification)
- Every task now has a **task_brief**: user request + command types + AI-designed steps + conclusions
- Task brief is passed to AI on every call — AI never loses context or forgets the goal
- Think steps: AI reasoning that happens during planning, conclusions guide subsequent actions

### Type-Driven Task Classification
- Tasks classified as query / create / modify before execution
- Pure query tasks skip user confirmation (zero risk, fast track)
- Mixed tasks: query phases run automatically, create/modify phases need user approval

### Three-Level Recovery
- **L1 (command level)**: retry + modify within the execution chain. 2 retries, 2 modifications
- **L2 (diagnostic)**: run diagnostic queries to understand why it failed, then write alternative approach. 3 attempts
- **L3 (redesign)**: gather expanded system info, AI designs fundamentally different approach. 2 attempts
- Every recovery level starts with a query — diagnose before acting, never guess
- Only asks user after all 3 levels exhausted (up to 9 attempts per step)

### AI Model Switching via Telegram
- `/model` command: switch AI provider from within Telegram chat
- Supports: OpenAI GPT-4o, DeepSeek, Claude, Gemini, Grok
- API key validated before saving (test request). Keys stored per provider
- Hot-switch: no restart needed

### DeepSeek Support
- Added DeepSeek (deepseek-chat) as AI provider option
- OpenAI-compatible API, lower cost, relaxed rate limits

### Improved Rate Limiting
- API call interval: 1.5s → 3s (prevents 429 on lower-tier accounts)
- Retry attempts: 3 → 5
- Project AI call limit: 100 → 1000

### UX Improvements
- Quick query failures no longer show technical errors — gracefully suggests creating a task
- Telegram reply/quote support: AI sees quoted message context
- Image support: photos analyzed via AI vision API
- File support: text files read and sent as context to AI

### No More Recursive Stack Overflow
- `_execute_node` rewritten from recursion to loop (max 10 attempts hard cap)

---

## V12.1 — 2026-03-14

### Dual AI Architecture
- Front-end AI (chat): read-only access, 20-turn memory, natural language interaction
- Back-end AI (execution): JSON-only communication, full read-write access

### Recursive Decomposition
- Tasks broken down layer by layer until every branch is a single executable command
- Depth-first traversal, safety valves as backstop

### Mechanical Verification
- 8 verifier types, zero AI tokens
- Verify effect principle: check the effect of an action, not the artifact

### Multi-Provider AI Support
- OpenAI GPT-4o, Anthropic Claude, Google Gemini, xAI Grok

### Multi-Language Support
- English and Chinese interface via i18n JSON files

### Other
- Zero third-party Python dependencies
- Interactive setup wizard
- systemd service file
- Telegram bot menu auto-configured on startup

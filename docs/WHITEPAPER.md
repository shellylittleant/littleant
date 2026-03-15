# LittleAnt V14 — Whitepaper

**AI Intent Execution System**

*Compile natural language intent into executable, verifiable, recoverable real-world actions via cycle-based execution.*

V14 | March 15, 2026 | Cycle Execution Architecture

---

## Version History

- **V8**: "AI Company" — solved problems with more AI. PM AI, planner AI, reviewer AI, executor AI, plus AI meetings. High cost, long chains, many failure points.
- **V11**: "AI Minimization" — right direction but stayed at the principle level. Lacked engineering details for task chain generation, user interaction, and error recovery.
- **V12**: Established dual AI architecture: front-end AI (chat) + back-end AI (execute) + recursive decomposition + mechanical verification + command protocol.
- **V12.1**: Improved V12 with autonomous recovery, verify effect principle, template library.
- **V14**: Architecture-level change. Replaced recursive decomposition with **cycle-based execution model**: query → judge → act → repeat. Three-level recovery (L1 command / L2 diagnostic / L3 redesign). Dynamic query commands. Task brief as project specification.

### V14 Core Changes (from V12.1)

- **Cycle execution model**: No more one-shot decomposition. System runs in cycles: scan system state → AI judges gap to goal → AI writes action → execute → rescan. Every decision is based on real data, not predictions.
- **Dynamic query commands**: Query commands are not fixed. Each cycle, AI decides what to check based on what the next action needs. Queries serve decisions, not the other way around.
- **Task brief**: Every project has a specification document (user request + command types + designed steps + AI conclusions). All AI calls include the full brief as context. AI never loses sight of the goal.
- **Type-driven classification**: Tasks classified as query/create/modify. Pure query tasks skip confirmation. Mixed tasks: query phases auto-execute, create/modify phases need user approval.
- **Three-level recovery**: L1 command retry/modify → L2 diagnostic query + alternative → L3 expanded info + full redesign. Each level starts with a query. Never guesses. Only asks user after all levels exhausted.
- **AI model hot-switching**: Switch between GPT-4o/DeepSeek/Claude/Gemini/Grok from Telegram without restart. API keys validated and stored per provider.

---

## 1. Design Philosophy

Core metaphor: **AI is the operator, Program is the computer.**

AI sits in front of the computer. The keyboard and mouse are everything AI can do — finite, predefined, each key has a clear function. The display shows execution results — AI reads the screen and decides which key to press next.

**Four Core Principles:**
1. Program handles execution and verification. AI handles thinking and decisions.
2. AI builds the execution chain through dialogue, not a one-shot perfect plan.
3. All verification is mechanical, zero token consumption. AI only intervenes on confirmed failures.
4. The system evolves through experience accumulation. Gets faster and cheaper with use.

---

## 2. Core Architecture: Dual AI Butler Model

### 2.1 Four Roles

| Role | Assigned To | Permissions | Responsibilities |
|------|------------|-------------|------------------|
| User (Boss) | Human | Final decision | Requirements, approvals, dangerous operations |
| Front-end AI (Butler) | AI | Read-only + initiate tasks | Understand needs, query status, report results |
| Back-end AI (Executor) | AI | Read-write + execute | Decompose tasks, generate commands, handle failures |
| Program (Computer) | LittleAnt Core | Execute commands | Schedule tasks, run commands, verify results, persist state |

### 2.2 Front-end vs Back-end AI

**Front-end AI (Chat AI)**: Talks to users in natural language. Has memory (20-turn context). Can query databases, run read-only commands (cat, ls, free, crontab -l, systemctl status, etc.), read files. Cannot write files, install software, change configs, or delete anything.

**Back-end AI (Execution AI)**: Only communicates with program via JSON. No memory. Handles recursive decomposition, executable command generation, and autonomous failure recovery. Full read-write execution permissions.

**Division principle**: Read-only → front-end AI does it directly. Needs modification → goes to back-end AI. Criterion: does the command have side effects?

### 2.3 Three Communication Channels

- **User ↔ Front-end AI**: Natural language
- **Front-end AI ↔ Program**: Read-only queries (database lookups, safe commands)
- **Back-end AI ↔ Program**: Structured command protocol (JSON only, never natural language)

---

## 3. Task Mode Trigger

**Core principle: Tasks must be explicitly requested by the user. Front-end AI never creates tasks on its own.**

| Type | Example | Front-end AI Response |
|------|---------|----------------------|
| Chat / Question | "How to configure nginx?" | Answer directly, no task mode |
| Might be a task | "I want to set up a blog" | Confirm: "Do you want me to execute this, or just asking?" |
| Explicit task | "Help me install WordPress" | Double confirm, then create task |
| Query progress | "How's that project going?" | Query database, report results |
| Quick query | "What's in crontab?" | Run read-only command directly, return result |

**Task creation flow**: User expresses execution intent → Front-end AI confirms ("Do you want me to execute this?") → User confirms → Back-end AI plans project → Show execution plan → User approves → Silent background execution → Front-end AI reports results

---

## 4. Recursive Decomposition

Core innovation: Back-end AI doesn't output the complete execution chain at once. Program takes the framework from AI and **asks layer by layer, from coarsest to finest, until every branch produces an executable command.**

### Process Demo

User: "Help me install WordPress for a shopping site, domain example.com"

**Layer 0**: Back-end AI generates project framework
```
Project: Website / WordPress / Shopping Theme / example.com
├── 1. Software Installation
├── 2. Shopping Theme Design
└── 3. Acceptance Testing
```

**Layer 1**: Program asks about each phase → AI returns subtasks
```json
{"cmd": "subtasks", "children": [
  {"id": "1.1", "name": "Environment Setup", "depends_on": []},
  {"id": "1.2", "name": "WordPress Installation", "depends_on": ["1.1"]},
  {"id": "1.3", "name": "Domain Binding", "depends_on": ["1.2"]}
]}
```

**Layer 2-3**: Drill down to executable commands
```json
{"cmd": "executable", "node_id": "1.1.1",
  "execute": {"type": "run_shell", "command": "apt-get install -y nginx"},
  "verify": {"type": "return_code_eq", "command": "nginx -t", "expected_code": 0},
  "on_fail": "report"}
```

Note: verify checks `nginx -t` return code (config syntax correct), not just whether nginx is running. This is the **verify effect principle**.

### Termination Conditions
- AI replies `cmd: "executable"` → leaf node, stop asking
- AI replies `cmd: "subtasks"` → recurse into each child
- Other format → ask AI to re-answer

### Core Pseudocode
```python
def decompose(node):
    response = ask_ai(node)
    if response.cmd == "executable":
        save_to_execution_queue(response)
        return
    for child in response.children:
        decompose(child)
```

All "intelligence" is on the AI side. All "certainty" is on the program side.

---

## 5. Command Protocol: Keyboard & Display

### 5.1 Back-end AI → Program Commands (Keyboard)

| Command | Category | Description |
|---------|----------|-------------|
| create_project | Task | Create project with framework |
| subtasks | Task | Reply with subtask list (non-leaf) |
| executable | Task | Reply with executable JSON (leaf) |
| modify | Task | Modify a node's command or verify |
| query | Task | Query project/node status |
| execute | Task | Start execution |
| stop | Task | Stop execution |
| resume | Task | Resume paused task |
| switch_to_user | Dialog | Switch to user channel |
| switch_to_program | Dialog | Switch to program channel |
| confirm / deny / cancel | Flow | Flow control |
| retry | Recovery | Retry failed node |
| skip | Recovery | Delete failed node from chain |
| abort | Recovery | Terminate project |
| report_to_user | Recovery | Escalate to user (safety valve only) |
| query_template | Library | Search template library (3 levels) |
| use_template | Library | Use historical template |
| save_template | Library | Save execution chain as template |

### 5.2 Program → Back-end AI Feedback (Display)

| Feedback | Description |
|----------|-------------|
| decompose | Ask AI to decompose a node (includes depth, context) |
| node_success | Node executed successfully |
| node_failed | Node failed (includes project tree for root cause tracing) |
| project_status | Overall project status |
| user_message | Forward user message |
| template_result | Template query result (snapshot_list / tree / nodes) |
| format_error | AI's last command was invalid format |

**Key principle**: The operator can be swapped, the keyboard stays the same. GPT today, Claude tomorrow, Grok next week — command protocol doesn't change.

---

## 6. Mechanical Verification

**Core conclusion: No command is "unverifiable."** Local commands check local results. External dependencies check via external APIs. Third-party returns 200? That's success. Everything after that is not the program's concern. AI output quality? That's the AI's business.

### 6.1 Verify Effect Principle

**Verify should check the effect of an action, not the artifact.**

| Action | ✘ Check Artifact | ✔ Check Effect |
|--------|-----------------|----------------|
| Write nginx config | file_exists nginx.conf | return_code_eq nginx -t → 0 |
| Install mysql | file_exists /usr/bin/mysql | service_active mysql |
| Send email | http_status_eq API → 200 | Query email API confirm sent |
| Run DB migration | return_code_eq → 0 | Check table has new columns |

This principle also solves: **crash recovery** (re-run verify checks real state) and **irreversible operations** (verify determines if operation already completed).

---

## 7. Error Recovery

### 7.1 Autonomous Recovery

Back-end AI handles failures on its own. Receives error + project tree, decides retry/modify/skip independently. Only escalates to user when safety limits are triggered.

### 7.2 Skip = Delete Node

Skip doesn't mark — it removes the node from the chain. Downstream dependencies update automatically. Execution continues.

### 7.3 Safety Valves
- **Per-node max retries**: 2. Exhausted → auto-skip
- **Per-node max modifications**: 2. Exhausted → auto-skip
- **Project max consecutive failures**: 5. Exceeded → escalate to user

---

## 8. State Persistence & Crash Recovery

| Data Type | Storage | Reason |
|-----------|---------|--------|
| Project tree, execution chain | JSON files | Natural tree structure |
| Execution logs | Database | High-volume read/write |
| Template & tool library | Database | Needs search/match |

**Crash recovery**: Walk the chain from the start, run verify on each node. First node that fails verify = resume point. Task checkpoint and verify checkpoint always coincide.

---

## 9. System Evolution

### 9.1 Dual Library Accumulation

- **Template Library**: Successful projects auto-save their full execution chain. Similar future tasks can reference and reuse.
- **Tool Command Library**: Scripts/tools created during projects are auto-cataloged (name, path, usage). Future tasks can directly invoke existing tools.

### 9.2 Three-Level Template Drill-Down

1. AI searches → program returns snapshot list (name, date, node count)
2. AI selects one → program returns tree structure (no commands)
3. AI decides which branch to inspect → program returns specific commands for that branch only

---

## 10. Security

| Level | Examples | Rule |
|-------|----------|------|
| L1 Safe | read files, check status, ls, cat | Auto-execute. Front-end AI can run directly |
| L2 Restricted | create dirs, write files, install software | Must go through back-end AI, logged |
| L3 Dangerous | delete files, change permissions, alter DB schema | Requires user confirmation |

---

## 11. User Interaction

### Front-end AI: Three Response Modes

1. **Database query (0 tokens)**: User asks about history → program queries DB → front-end AI translates to plain language
2. **Quick read-only command (0 tokens)**: User wants current status → program runs safe command → front-end AI translates result
3. **Formal task (consumes tokens)**: User explicitly requests execution → double confirm → back-end AI plans + executes → front-end AI reports

### Silent Execution

Background execution doesn't flood the user with messages. Progress is written to database. Only three things interrupt the user: dangerous operation confirmation, task completion, safety valve triggered.

---

## 12. Implementation Roadmap

**P0: Must build first**
1. Command protocol with JSON Schema validation
2. Recursive decomposition engine
3. Execution engine + mechanical verification (effect principle)
4. Error recovery (project tree in failures, safety valves)
5. State persistence (JSON + DB, crash recovery)
6. Dual AI architecture (front-end read-only + back-end execute)

**P1: Required for production**
1. User interaction layer (API + Web console)
2. Security & permissions (command levels, whitelist, sandbox)
3. Cold-start seed library

**P2: Future iterations**
1. Parallel execution (next supports arrays + join nodes)
2. Multi-AI model switching (different models for different tasks)
3. Intelligent template matching

---

## 13. V8 / V11 / V14 Comparison

| Dimension | V8 | V11 | V14 |
|-----------|-----|------|-------|
| Dimension | V8 | V11 | V12.1 | V14 |
|-----------|-----|------|-------|-----|
| AI roles | 5+ (PM, planner, reviewer, executor, auditor) | 2 (Planner, Reviewer) | 2 (Front-end read-only + Back-end execute) | 2 (Front-end chat + Back-end execute) |
| Execution model | PM AI + 4-role pipeline | Planner AI one-shot | Recursive decomposition, layer by layer | Cycle: query → judge → act → repeat |
| Planning | One-shot full plan | One-shot full plan | Recursive, all at once | Per-cycle, based on real data |
| Verification | AI reviews AI | Program-first + AI backup | Pure mechanical + effect principle | Mechanical + cycle re-query as verification |
| Failure handling | PM AI decides (no global view) | Undefined after report | AI autonomous recovery | 3-level: L1 retry → L2 diagnose → L3 redesign |
| Task trigger | PM AI decides | Undefined | User explicit + double confirm | Type-driven: query auto, create/modify confirm |
| Template reuse | Employee evaluation stats | Command library + case library | Three-level drill-down | Tool library + template library |

---

*— V14 - Cycle Execution Architecture —*

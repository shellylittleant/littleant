# Changelog

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

# Changelog

## v12.1 — 2026-03-14

### Dual AI Architecture
- Front-end AI (chat): read-only access, 20-turn memory, natural language interaction
- Back-end AI (execution): JSON-only communication, full read-write access
- Clear permission boundary: read-only operations stay with front-end, modifications go to back-end

### Recursive Decomposition
- Tasks broken down layer by layer until every branch is a single executable command
- Depth-first traversal, natural convergence, safety valves as backstop
- Existing project tree sent as context to prevent duplicate nodes

### Autonomous Error Recovery
- Back-end AI handles failures independently (retry → modify → skip)
- Only escalates to user when safety limits are hit (5 consecutive failures)
- Skip = delete node from chain (not mark), downstream dependencies auto-update

### Verify Effect Principle
- Verification checks the **effect** of an action, not the **artifact**
- Example: after writing nginx config, verify with `nginx -t`, not `file_exists`
- Solves crash recovery and irreversible operation problems simultaneously

### Tool & Template Library
- Successful projects auto-save execution chains to template library
- Scripts and tools auto-cataloged (name, path, usage)
- Future tasks search library first, reuse existing tools

### Multi-Provider AI Support
- OpenAI GPT-4o, Anthropic Claude, Google Gemini, xAI Grok
- API rate limiting (1.5s interval) with automatic 429 retry
- Provider selected during setup, no code changes needed

### User Interaction
- Task creation requires explicit user instruction + double confirmation
- Execution runs silently in background, progress in database
- Front-end AI summarizes results in plain language
- Read-only quick queries (crontab -l, free -h, etc.) execute instantly

### Multi-Language Support
- English and Chinese interface
- Language selected during setup
- All user-facing strings in i18n JSON files

### Other
- Zero third-party Python dependencies (pure stdlib)
- Interactive setup wizard (setup.py)
- systemd service file included
- Telegram bot menu auto-configured on startup

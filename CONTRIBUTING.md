# Contributing to LittleAnt

Thanks for your interest in contributing! LittleAnt welcomes contributions of all kinds.

## Ways to Contribute

- **Bug Reports** — Found something broken? Open an issue with steps to reproduce.
- **Feature Requests** — Have an idea? Open an issue and describe the use case.
- **Code** — Fix a bug or implement a feature. See the workflow below.
- **Documentation** — Improve README, whitepaper, or add examples.
- **Translations** — Add a new language file in `littleant/i18n/`.
- **AI Provider Support** — Add adapter for a new AI provider.

## Development Setup

```bash
git clone https://github.com/shellylittleant/littleant.git
cd littleant
python3 setup.py          # Configure with your tokens
python3 run.py             # Run directly (no build step)
```

No dependencies to install. Pure Python 3.10+ stdlib.

## Pull Request Workflow

1. Fork the repository
2. Create a feature branch: `git checkout -b feature/your-feature`
3. Make your changes
4. Test: make sure `python3 run.py` starts without errors
5. Commit with a clear message: `git commit -m "Add: new verifier for X"`
6. Push and open a PR

## Code Guidelines

- **Zero dependencies**: Don't add `pip install` requirements. Use stdlib only.
- **English code**: All code, comments, logs, and docstrings in English.
- **User-facing text**: Goes in `littleant/i18n/en.json` and `littleant/i18n/zh.json`, not hardcoded.
- **AI prompts**: Keep in `littleant/ai/adapter.py`. Prompts are always English (AI works better).
- **New commands**: Add to `littleant/core/protocol.py` command set + validator.
- **New verifiers**: Add to `littleant/core/verifier.py` following the existing pattern.

## Adding a Language

1. Copy `littleant/i18n/en.json` to `littleant/i18n/xx.json` (your language code)
2. Translate all values (keep keys unchanged)
3. Add the language option in `setup.py`
4. Submit a PR

## Architecture Quick Reference

```
User ↔ Front-end AI (read-only) ↔ Program ↔ Back-end AI (execute)
```

- **Front-end AI**: `run.py` — chat, intent classification, read-only queries
- **Back-end AI**: `littleant/ai/adapter.py` — task decomposition, command generation
- **Core engine**: `littleant/core/` — orchestrator, decomposer, executor, verifier, recovery
- **Protocol**: `littleant/core/protocol.py` — 20 AI→Program commands, 7 Program→AI feedbacks
- **Storage**: `littleant/storage/` — JSON files for project trees, SQLite for logs & templates
- **i18n**: `littleant/i18n/` — all user-facing strings

## Commit Message Convention

```
Add: new feature
Fix: bug fix
Docs: documentation change
Refactor: code restructuring
i18n: translation update
```

## Questions?

Open an issue or start a discussion. We're friendly.

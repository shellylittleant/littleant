# 🐜 LittleAnt V12.1

**AI Intent Execution System** — Compile natural language intent into executable, verifiable, recoverable real-world actions via recursive decomposition.

LittleAnt is an AI butler that lives on your server. You talk to it through Telegram, and it executes tasks on your server autonomously — installing software, configuring services, writing scripts, and more.

## Architecture

```
User ←→ Front-end AI (chat, read-only) ←→ Program ←→ Back-end AI (execute, read-write)
```

- **Front-end AI**: Chats with users, understands intent, has memory (20-turn context), can run read-only commands, reports results in plain language
- **Back-end AI**: Only communicates with the program via JSON, decomposes tasks recursively, generates executable commands, handles failures autonomously
- **Program**: Executes commands, performs mechanical verification, persists state, manages the task lifecycle

## Key Features

- **Recursive Decomposition**: Tasks are broken down layer by layer until every branch becomes an executable command
- **Mechanical Verification**: All verification is done by the program (zero AI tokens). AI only intervenes on confirmed failures
- **Autonomous Recovery**: Back-end AI handles failures (retry/modify/skip) on its own. Only escalates to user when safety limits are hit
- **Tool Library**: Successfully completed tasks are automatically saved. Next time a similar task comes up, AI reuses existing tools instead of starting from scratch
- **Dual AI Architecture**: Front-end AI has read-only access, back-end AI has full execution rights. Clear permission boundary
- **Multi-language**: Supports English and Chinese interface

## Supported AI Providers

| Provider | Model |
|----------|-------|
| OpenAI | GPT-4o |
| Anthropic | Claude Sonnet |
| Google | Gemini 2.0 Flash |
| xAI | Grok 3 |

## Quick Start

### 1. Requirements
- Python 3.10+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- An API key from any supported AI provider

### 2. Install & Setup

```bash
git clone https://github.com/yourusername/littleant.git
cd littleant
python3 setup.py
```

The setup wizard will ask you to:
1. Choose language (English / 中文)
2. Enter your Telegram Bot Token
3. Select AI provider (OpenAI / Claude / Gemini / Grok)
4. Enter your API key

### 3. Run

```bash
bash start.sh
```

### 4. Run as a Service (recommended for production)

```bash
cp littleant.service /etc/systemd/system/
# Edit WorkingDirectory in the service file to match your install path
systemctl daemon-reload
systemctl enable littleant
systemctl start littleant
```

## Usage

Open Telegram, find your bot, and start chatting:

| What you say | What happens |
|-------------|-------------|
| "How do I configure nginx?" | AI answers directly (chat mode) |
| "Help me install LNMP" | AI confirms → plans → you approve → executes silently → reports results |
| "What's in crontab?" | AI runs `crontab -l` directly (read-only) and tells you |
| /status | Shows current task progress |
| /cancel | Cancels current task |

## Project Structure

```
littleant/
├── setup.py                 Setup wizard (first-run config)
├── run.py                   Main entry point
├── start.sh                 One-command startup
├── littleant/
│   ├── i18n/                Language files (en.json, zh.json)
│   ├── ai/adapter.py        AI adapter (multi-provider, rate limiting)
│   ├── core/
│   │   ├── decomposer.py    Recursive decomposition engine
│   │   ├── executor.py      Command executor
│   │   ├── verifier.py      8 mechanical verifiers
│   │   ├── recovery.py      Autonomous error recovery
│   │   ├── orchestrator.py  Main orchestrator
│   │   ├── protocol.py      Command protocol (keyboard & display)
│   │   └── readonly_executor.py  Read-only executor for front-end AI
│   ├── models/project.py    Data models & state machine
│   └── storage/             JSON + SQLite persistence
└── littleant.service        systemd service file
```

## How It Works

1. **You say**: "Help me install WordPress"
2. **Front-end AI** confirms: "Do you want me to execute this?"
3. **You confirm**: "Yes"
4. **Back-end AI** generates a project framework, then the program recursively asks the AI to break it down until every branch is a single executable command
5. **You review** the execution plan and approve
6. **Program executes** each command silently, mechanically verifies each result
7. **If something fails**, back-end AI autonomously retries, modifies, or skips — only bothers you if safety limits are hit
8. **When done**, front-end AI summarizes results in plain language

## Design Philosophy

> AI is the operator. Program is the computer.

The keyboard (command set) is finite — AI can only choose from predefined commands. The display (feedback) shows execution results. The operator can be swapped (GPT today, Claude tomorrow) — the keyboard stays the same.

## License

MIT

## Contributing

Issues and PRs welcome. See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

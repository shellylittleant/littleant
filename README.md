<h1 align="center">🐜 LittleAnt V13</h1>

<p align="center"><strong>AI Intent Execution System</strong></p>

<p align="center">
  Compile natural language intent into executable, verifiable, recoverable server actions — via cycle-based execution.
</p>

<p align="center">
  <a href="https://github.com/shellylittleant/littleant/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue.svg" alt="MIT License"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.10+-green.svg" alt="Python 3.10+"></a>
  <a href="https://github.com/shellylittleant/littleant/releases"><img src="https://img.shields.io/github/v/release/shellylittleant/littleant" alt="Release"></a>
  <a href="https://github.com/shellylittleant/littleant/stargazers"><img src="https://img.shields.io/github/stars/shellylittleant/littleant?style=social" alt="Stars"></a>
</p>

<p align="center">
  English | <a href="./README_ZH.md">简体中文</a>
</p>

---

LittleAnt is an AI butler that lives on your server. You talk to it through Telegram, and it autonomously executes tasks — installing software, configuring services, writing scripts, monitoring systems, and more. Built on Python stdlib only, no third-party packages required.

## Screenshots

<table>
  <tr>
    <td align="center"><strong>Chat & Task Confirm</strong></td>
    <td align="center"><strong>Execution Plan</strong></td>
    <td align="center"><strong>Result Report</strong></td>
  </tr>
  <tr>
    <td><img src="docs/1.jpg" width="280"></td>
    <td><img src="docs/2.jpg" width="280"></td>
    <td><img src="docs/3.jpg" width="280"></td>
  </tr>
</table>

## Why LittleAnt?

Most AI agent frameworks stop at generating text. LittleAnt turns intent into real, verifiable server actions.

| | Chat-first Agents | LittleAnt |
|---|---|---|
| **Output** | Text / suggestions | Executable shell commands |
| **Verification** | None or AI-based | Mechanical (zero AI tokens) |
| **Failure handling** | Crash or ask user | AI self-recovers (retry → modify → skip) |
| **Task decomposition** | One-shot plan | Cycle: query → judge → act → repeat |
| **Transparency** | Black box | Full execution tree, every step logged |
| **Dependencies** | pip install dozens of packages | None. Pure Python stdlib |

## Architecture

```
┌─────────────┐     Natural Language    ┌──────────────────┐
│    User     │◄──────────────────────► │  Front-end AI    │
│  (Telegram) │                         │  (Chat, ReadOnly)│
└─────────────┘                         └────────┬─────────┘
                                                 │ DB queries
                                                 │ Read-only commands
                                                 │ Initiate tasks
                                        ┌────────▼─────────┐
                                        │   LittleAnt Core │
                                        │   (Orchestrator) │
                                        └────────┬─────────┘
                                                 │ JSON Protocol
                                        ┌────────▼─────────┐
                                        │  Back-end AI     │
                                        │  (Execute, R/W)  │
                                        └──────────────────┘
```

- **Front-end AI** — Chats with users, has memory (20-turn context), runs read-only commands, reports results in plain language. Cannot modify anything.
- **Back-end AI** — JSON only, no memory. Writes queries, judges system state, plans actions, handles failures via 3-level recovery. Full read-write access.
- **Core** — Executes commands, mechanically verifies results, persists state. All verification is code-based, zero AI tokens.

## Supported AI Providers

| Provider | Model | Status |
|----------|-------|--------|
| OpenAI | GPT-4o | ✅ Tested |
| DeepSeek | DeepSeek-chat | ✅ Supported |
| Anthropic | Claude Sonnet | ✅ Supported |
| Google | Gemini 2.0 Flash | ✅ Supported |
| xAI | Grok 3 | ✅ Supported |

Any OpenAI-compatible API endpoint works. Actual behavior may vary by model quality and API compatibility.

## Quick Start

### Requirements
- Python 3.10+ (no pip install needed)
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- An API key from any supported provider

### Install & Setup

```bash
git clone https://github.com/shellylittleant/littleant.git
cd littleant
python3 setup.py
```

The interactive wizard asks:
1. 🌐 Language (English / 中文)
2. 🤖 Telegram Bot Token
3. 🧠 AI Provider (OpenAI / Claude / Gemini / Grok)
4. 🔑 API Key

### Run

```bash
bash start.sh
```

### Run as a Service (recommended)

```bash
cp littleant.service /etc/systemd/system/
# Edit WorkingDirectory in the service file to match your install path
systemctl daemon-reload
systemctl enable littleant
systemctl start littleant
```

## Usage

| What you say | What happens |
|---|---|
| "How do I configure nginx?" | AI answers directly (chat mode) |
| "Help me install LNMP" | Confirms → plans → you approve → executes → reports |
| "What's in crontab?" | Runs `crontab -l` directly (read-only), tells you the result |
| "What's my disk usage?" | Runs `df -h`, summarizes in plain language |
| /status | Shows current task progress |
| /cancel | Cancels current task |

### How a Task Executes

```
1. You: "Help me install WordPress"
2. Front-end AI: "Do you want me to execute this?" → You confirm
3. Back-end AI designs steps, then scans current system state
4. AI judges: "nginx not installed, PHP not installed" → writes install commands
5. AI reviews commands → shows you the plan → you approve → executes
6. AI rescans system → "nginx installed, PHP installed, need to configure"
7. AI writes config commands → you approve → executes
8. AI rescans → everything matches goal → done
```

## Real-World Use Cases

LittleAnt has already been used in real server workflows, including:

- **LNMP Stack** — Nginx + MySQL + PHP installed, configured, and verified in one conversation
- **Server Monitoring** — Custom monitoring plugin written and deployed
- **Scheduled Tasks** — Cron jobs configured with Telegram notifications
- **SSL & CDN** — Cloudflare integration plugin developed
- **Web Crawlers** — Spider tools built and executed
- **System Diagnostics** — Full server audit with plain-language report

## Project Structure

```
littleant/
├── setup.py                  # Interactive setup wizard
├── run.py                    # Main entry point
├── start.sh                  # One-command startup
├── littleant.service          # systemd service file
├── docs/
│   └── WHITEPAPER.md         # Full technical whitepaper
├── littleant/
│   ├── i18n/                 # Language files (en.json, zh.json)
│   ├── ai/adapter.py         # Multi-provider AI adapter (rate limiting, 429 retry)
│   ├── core/
│   │   ├── decomposer.py     # Step decomposition (legacy, used for complex tasks)
│   │   ├── executor.py       # Command executor
│   │   ├── verifier.py       # 8 mechanical verifiers
│   │   ├── recovery.py       # Node removal & crash recovery
│   │   ├── orchestrator.py   # Cycle engine + 3-level recovery (L1/L2/L3)
│   │   ├── protocol.py       # Command protocol (20 cmds + 7 feedbacks)
│   │   └── readonly_executor.py  # Read-only executor with whitelist
│   ├── models/project.py     # Data models & state machine
│   └── storage/              # JSON + SQLite hybrid persistence
└── README.md
```

## Design Principles

> **"AI is the operator. Program is the computer."**

- Command set is finite — AI chooses from predefined commands, can't invent new ones
- All verification is mechanical — zero AI token consumption
- Failed nodes are **deleted** from the chain, not marked — execution continues automatically
- Successful projects auto-save to template library — AI reuses them next time
- The operator can be swapped (GPT → Claude → Gemini) — the protocol stays the same

## Documentation

- [Whitepaper (English)](docs/WHITEPAPER.md) — Full architecture, protocol, and design specification
- [Changelog](CHANGELOG.md) — Release notes

## Contributing

Contributions welcome! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

## License

[MIT](LICENSE)

---

<p align="center">
  <sub>Built with cycle-based execution and mechanical verification.<br>No third-party dependencies. No trust in AI output.</sub>
</p>

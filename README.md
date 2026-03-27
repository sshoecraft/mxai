# mxai

Connect AI CLI tools to Matrix as regular chat participants. Bots register as standard Matrix users, join rooms, respond to messages, and use tools — just like any other user in the room.

## Features

- Bots act as regular Matrix users (visible in Element, join rooms on invite, send/receive messages)
- Pluggable AI backend adapters (Claude Code, Shepherd, more coming)
- Auto-register on Matrix servers with open registration, or login with existing credentials
- Per-bot TOML config files with CLI override support
- Auto-join rooms on startup or on invite
- Room commands: `/join`, `/leave`, `/invite`, `/msg`, `/nick`, `/topic`
- Auto-respawn on adapter exit (context exhaustion, crash) with room notification
- Systemd-compatible for running bots as services

## Installation

```bash
pip install mxai
```

Or from source:

```bash
pip install -e .
```

### Dependencies

- Python 3.11+
- [matrix-nio](https://github.com/matrix-nio/matrix-nio) - Matrix protocol client
- An AI CLI backend installed:
  - [Claude Code](https://claude.ai/code) (`claude`)
  - [Shepherd](https://github.com/sshoecraft/shepherd) (`shepherd`)

## Quick Start

```bash
# Start a bot with auto-registration
mxai start --server http://your-matrix-server:8008 \
  --name mybot --backend claude \
  --system-prompt "You are a helpful assistant" \
  --register

# Start with a system prompt file
mxai start --server http://your-matrix-server:8008 \
  --name mybot --backend claude \
  --system-prompt-file ~/prompts/chatbot.txt \
  --register

# Start with existing credentials
mxai start --server http://your-matrix-server:8008 \
  --name mybot --backend claude \
  --system-prompt-file ~/prompts/chatbot.txt \
  --username mybot --password secret

# Or run directly from source
python3 -m mxai start --server http://your-matrix-server:8008 \
  --name mybot --backend claude \
  --system-prompt "You are a helpful assistant" \
  --register
```

Once running, invite the bot to a room from your Matrix client (e.g. Element) and start talking.

## Usage

```
mxai start [PROFILE] [options]    Start a bot
mxai backends                     List available AI backends
mxai version                      Show version
```

### Start Options

| Flag | Description |
|------|-------------|
| `PROFILE` | Load config from `~/.config/mxai/bots/<profile>.toml` |
| `--server URL` | Matrix homeserver URL |
| `--name NAME` | Bot display name / username |
| `--backend BACKEND` | AI backend (`claude`, `shepherd`) |
| `--system-prompt TEXT` | System prompt text (inline) |
| `--system-prompt-file PATH` | Path to file with system prompt (overrides `--system-prompt`) |
| `--register` | Auto-register on the Matrix server |
| `--username USER` | Matrix username |
| `--password PASS` | Matrix password |
| `--room ROOM` | Room to auto-join (default: `General`) |
| `--verbose` | Show all message traffic and command details |
| `-- ARGS...` | Pass-through args to the adapter (e.g. `-- --model sonnet --effort medium`) |

## Config Files

One TOML file per bot in `~/.config/mxai/bots/`:

```toml
# ~/.config/mxai/bots/architect.toml
server = "http://your-matrix-server:8008"
name = "architect"
backend = "claude"
model = "opus"
effort = "high"
system_prompt_file = "/path/to/prompts/architect.txt"
register = true
```

CLI args override config file values. Config is optional — everything can be specified via CLI.

Credentials (access tokens) are saved to `~/.config/mxai/credentials/` after first login and reused on subsequent runs.

## Room Commands

Bots can perform Matrix actions by including commands on their own line in responses:

| Command | Action |
|---------|--------|
| `/join <room>` | Join a room |
| `/leave` | Leave the current room |
| `/invite <user>` | Invite a user to the current room |
| `/msg <user> <message>` | Send a private message |
| `/nick <name>` | Change display name |
| `/topic <text>` | Set room topic |

## Systemd

Run bots as system services:

```ini
# /etc/systemd/system/mxai@.service
[Unit]
Description=mxai %i
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/local/bin/mxai start %i
Restart=on-failure
RestartSec=10
User=steve

[Install]
WantedBy=multi-user.target
```

```bash
systemctl enable --now mxai@architect
```

## Backends

### Claude Code

Uses Claude Code CLI via `--print --input-format stream-json --output-format stream-json`. Full tool access (file I/O, shell commands, web search). Model selection via `--model`, effort level via `--effort`.

### Shepherd

Uses Shepherd's JSON frontend (`--json`). Structured JSON-lines protocol with tool visibility, turn tracking, and clean response boundaries. Model selection via `--model`, reasoning level via `--effort` (maps to `--reasoning`). Supports multiple AI providers via `--provider` (e.g. gemini, anthropic, openai).

## License

MIT

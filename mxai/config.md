# config.py — XDG Config Loading

## Purpose
Loads per-bot TOML config files from XDG-compliant paths. Config is optional — all values can come from CLI args.

## File locations
- Config dir: `$XDG_CONFIG_HOME/mxai/` (default: `~/.config/mxai/`)
- Bot configs: `bots/{name}.toml`
- Credentials: `credentials/{username}.json`

## Config format
```toml
server = "http://192.168.1.166:8008"
name = "claude-bot"
backend = "claude"
role = "You are a helpful assistant"
register = true
# OR:
# username = "claude-bot"
# password = "secret"
```

## Merge order
CLI args override config file values. `merge_config(file_config, cli_args)` skips None values from CLI (not set by user).

## History
- v0.1.0 (2026-03-25): Initial implementation with tomllib (Python 3.11+ stdlib).

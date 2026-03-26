# cli.py — CLI Entry Point

## Purpose
Argparse-based CLI for mxai. Entry point registered as `mxai` console script via pyproject.toml.

## Commands
- `mxai start [PROFILE] [options]` — start a bot
- `mxai backends` — list available AI backends
- `mxai version` — show version

## Signal handling
Uses asyncio event loop signal handlers (`loop.add_signal_handler`) for clean shutdown. SIGINT/SIGTERM set a stop event, which races against the bot task via `asyncio.wait(FIRST_COMPLETED)`.

## Config resolution
1. If PROFILE given, load `~/.config/mxai/bots/{PROFILE}.toml`
2. CLI args override config file values
3. Validate required fields: server, name, backend, role

## History
- v0.1.0 (2026-03-25): Initial implementation with async signal handling.

"""matrixbot CLI — connect AI backends to Matrix.

Usage:
    matrixbot start [PROFILE] [--server URL] [--name NAME] [--backend BACKEND]
                    [--role ROLE] [--register] [--username U] [--password P]
    matrixbot backends
    matrixbot version

v0.1.1
"""

import argparse
import asyncio
import signal
import sys

from . import VERSION
from .adapters import list_backends
from .bot import MatrixBot
from .config import load_bot_config, merge_config


def cmd_start(args):
    """Start a bot and connect it to Matrix."""
    # Load config from profile if specified
    file_config = {}
    if args.profile:
        file_config = load_bot_config(args.profile)
        if not file_config:
            print(f"No config file found for profile '{args.profile}'")
            print(f"Checked: ~/.config/matrixbot/bots/{args.profile}.toml")

    # CLI overrides
    cli_args = {
        "server": args.server,
        "name": args.name,
        "backend": args.backend,
        "role": args.role,
        "register": args.register if args.register else None,
        "username": args.username,
        "password": args.password,
        "model": args.model,
        "effort": args.effort,
        "room": args.room,
    }

    config = merge_config(file_config, cli_args)

    # Load role from file if specified
    role_file = args.role_file or config.get("role_file")
    if role_file:
        try:
            with open(role_file) as f:
                config["role"] = f.read().strip()
        except FileNotFoundError:
            print(f"Role file not found: {role_file}")
            sys.exit(1)

    # Validate required fields
    required = ["server", "name", "backend", "role"]
    missing = [f for f in required if not config.get(f)]
    if missing:
        print(f"Missing required config: {', '.join(missing)}")
        print("Provide via config file or CLI args: "
              + ", ".join(f"--{f}" for f in missing))
        sys.exit(1)

    bot = MatrixBot(
        homeserver=config["server"],
        name=config["name"],
        backend=config["backend"],
        role=config["role"],
        username=config.get("username"),
        password=config.get("password"),
        do_register=config.get("register", False),
        model=config.get("model"),
        effort=config.get("effort"),
        room=config.get("room", "Lobby"),
    )

    print(f"matrixbot v{VERSION}", flush=True)
    print(f"  server: {config['server']}", flush=True)
    print(f"  name: {config['name']}", flush=True)
    print(f"  backend: {config['backend']}", flush=True)
    if config.get("model"):
        print(f"  model: {config['model']}", flush=True)
    if config.get("effort"):
        print(f"  effort: {config['effort']}", flush=True)

    async def run():
        loop = asyncio.get_running_loop()
        stop_event = asyncio.Event()

        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop_event.set)

        # Start bot in a task so we can also await the stop event
        bot_task = asyncio.create_task(bot.start())

        # Wait for either the bot to exit or a signal
        stop_task = asyncio.create_task(stop_event.wait())
        done, pending = await asyncio.wait(
            [bot_task, stop_task],
            return_when=asyncio.FIRST_COMPLETED,
        )

        for t in pending:
            t.cancel()

        print(f"\n  [{config['name']}] shutting down...", flush=True)
        await bot.stop()

    try:
        asyncio.run(run())
    except Exception as e:
        print(f"  Error: {e}", flush=True)
        sys.exit(1)


def cmd_backends(args):
    """List available backends."""
    backends = list_backends()
    print("Available backends:")
    for b in backends:
        print(f"  - {b}")


def cmd_version(args):
    """Show version."""
    print(f"matrixbot v{VERSION}")


def main():
    parser = argparse.ArgumentParser(
        prog="matrixbot",
        description="Connect AI CLI tools to Matrix as regular chat participants",
    )
    sub = parser.add_subparsers(dest="command")

    # start
    start_parser = sub.add_parser("start", help="Start a bot")
    start_parser.add_argument(
        "profile", nargs="?", default=None,
        help="Bot profile name (loads ~/.config/matrixbot/bots/<profile>.toml)")
    start_parser.add_argument("--server", "-s", default=None,
                              help="Matrix homeserver URL")
    start_parser.add_argument("--name", "-n", default=None,
                              help="Bot display name")
    start_parser.add_argument("--backend", "-b", default=None,
                              help="AI backend (claude, shepherd)")
    start_parser.add_argument("--role", "-r", default=None,
                              help="Role description (short string)")
    start_parser.add_argument("--role-file", default=None,
                              help="Path to file with full role instructions (overrides --role)")
    start_parser.add_argument("--register", action="store_true", default=False,
                              help="Auto-register on the Matrix server")
    start_parser.add_argument("--username", "-u", default=None,
                              help="Matrix username")
    start_parser.add_argument("--password", "-p", default=None,
                              help="Matrix password")
    start_parser.add_argument("--model", "-m", default=None,
                              help="Model name (e.g. sonnet, opus, gpt-4o)")
    start_parser.add_argument("--effort", "-e", default=None,
                              help="Effort/reasoning level (low, medium, high, max)")
    start_parser.add_argument("--room", default=None,
                              help="Room to auto-join (default: Lobby)")

    # backends
    sub.add_parser("backends", help="List available AI backends")

    # version
    sub.add_parser("version", help="Show version")

    args = parser.parse_args()

    if args.command == "start":
        cmd_start(args)
    elif args.command == "backends":
        cmd_backends(args)
    elif args.command == "version":
        cmd_version(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()

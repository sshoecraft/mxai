"""mxai CLI — connect AI backends to Matrix.

Usage:
    mxai start [--profile PROFILE] [--server URL] [--name NAME] [--backend BACKEND]
               [--system-prompt TEXT] [--system-prompt-file FILE]
               [--register] [--username U] [--password P] [ADAPTER_ARGS...]
    mxai backends
    mxai version

v0.2.0
"""

import argparse
import asyncio
import signal
import sys

from . import VERSION
from .adapters import list_backends
from .bot import MXAI
from .config import load_bot_config, merge_config


def cmd_start(args):
    """Start a bot and connect it to Matrix."""
    # Load config from profile if specified
    file_config = {}
    if args.profile:
        file_config = load_bot_config(args.profile)
        if not file_config:
            print(f"No config file found for profile '{args.profile}'")
            print(f"Checked: ~/.config/mxai/bots/{args.profile}.toml")

    # CLI overrides
    cli_args = {
        "server": args.server,
        "name": args.name,
        "backend": args.backend,
        "system_prompt": args.system_prompt,
        "register": args.register if args.register else None,
        "username": args.username,
        "password": args.password,
        "room": args.room,
        "verbose": args.verbose if args.verbose else None,
        "debug": args.debug if args.debug else None,
    }

    config = merge_config(file_config, cli_args)

    # Load system prompt from file if specified
    prompt_file = args.system_prompt_file or config.get("system_prompt_file")
    if prompt_file:
        try:
            with open(prompt_file) as f:
                config["system_prompt"] = f.read().strip()
        except FileNotFoundError:
            print(f"System prompt file not found: {prompt_file}")
            sys.exit(1)

    # Validate required fields
    required = ["server", "name", "backend"]
    missing = [f for f in required if not config.get(f)]
    if missing:
        print(f"Missing required config: {', '.join(missing)}")
        print("Provide via config file or CLI args: "
              + ", ".join(f"--{f}" for f in missing))
        sys.exit(1)

    bot = MXAI(
        homeserver=config["server"],
        name=config["name"],
        backend=config["backend"],
        system_prompt=config.get("system_prompt", ""),
        username=config.get("username"),
        password=config.get("password"),
        do_register=config.get("register", False),
        room=config.get("room", "General"),
        verbose=config.get("verbose", False),
        debug=config.get("debug", False),
        extra_args=config.get("adapter_args", []) + args.extra_args,
    )

    print(f"mxai v{VERSION}", flush=True)
    print(f"  server: {config['server']}", flush=True)
    print(f"  name: {config['name']}", flush=True)
    print(f"  backend: {config['backend']}", flush=True)


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
    print(f"mxai v{VERSION}")


def main():
    parser = argparse.ArgumentParser(
        prog="mxai",
        description="Connect AI CLI tools to Matrix as regular chat participants",
    )
    sub = parser.add_subparsers(dest="command")

    # start
    start_parser = sub.add_parser("start", help="Start a bot")
    start_parser.add_argument("profile", nargs="?", default=None,
                              help="Bot profile name (loads ~/.config/mxai/bots/<profile>.toml)")
    start_parser.add_argument("--server", "-s", default=None,
                              help="Matrix homeserver URL")
    start_parser.add_argument("--name", "-n", default=None,
                              help="Bot display name")
    start_parser.add_argument("--backend", "-b", default=None,
                              help="AI backend (claude, shepherd)")
    start_parser.add_argument("--system-prompt", default=None,
                              help="System prompt text (inline)")
    start_parser.add_argument("--system-prompt-file", default=None,
                              help="Path to file with system prompt (overrides --system-prompt)")
    start_parser.add_argument("--register", action="store_true", default=False,
                              help="Auto-register on the Matrix server")
    start_parser.add_argument("--username", "-u", default=None,
                              help="Matrix username")
    start_parser.add_argument("--password", "-p", default=None,
                              help="Matrix password")
    start_parser.add_argument("--room", default=None,
                              help="Room to auto-join (default: General)")
    start_parser.add_argument("--verbose", "-v", action="store_true", default=False,
                              help="Show all message traffic and command details")
    start_parser.add_argument("--debug", "-d", action="store_true", default=False,
                              help="Show full system prompt, adapter command, and enable verbose")

    # backends
    sub.add_parser("backends", help="List available AI backends")

    # version
    sub.add_parser("version", help="Show version")

    # Split on -- to separate mxai args from adapter pass-through args
    argv = sys.argv[1:]
    if "--" in argv:
        split = argv.index("--")
        extra = argv[split + 1:]
        argv = argv[:split]
    else:
        extra = []

    args = parser.parse_args(argv)

    if args.command == "start":
        args.extra_args = extra
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

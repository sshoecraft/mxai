"""XDG-compliant config loading for matrixbot.

Config files are TOML, one per bot, stored in:
    ~/.config/matrixbot/bots/{name}.toml

Credentials (access tokens) stored in:
    ~/.config/matrixbot/credentials/{username}.json

v0.1.0
"""

import os
import tomllib


def get_config_dir() -> str:
    """Return the matrixbot config directory, respecting XDG_CONFIG_HOME."""
    xdg = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    return os.path.join(xdg, "matrixbot")


def get_bots_dir() -> str:
    """Return the directory for bot config files."""
    return os.path.join(get_config_dir(), "bots")


def get_credentials_dir() -> str:
    """Return the directory for credential files."""
    return os.path.join(get_config_dir(), "credentials")


def ensure_config_dirs():
    """Create config directories if they don't exist."""
    os.makedirs(get_bots_dir(), exist_ok=True)
    os.makedirs(get_credentials_dir(), exist_ok=True)


def load_bot_config(bot_name: str) -> dict:
    """Load a bot's config from its TOML file.

    Returns empty dict if file doesn't exist.
    """
    path = os.path.join(get_bots_dir(), f"{bot_name}.toml")
    if not os.path.exists(path):
        return {}
    with open(path, "rb") as f:
        return tomllib.load(f)


def merge_config(file_config: dict, cli_args: dict) -> dict:
    """Merge config file values with CLI args. CLI args take precedence.

    CLI args that are None are ignored (not set by user).
    """
    merged = dict(file_config)
    for key, value in cli_args.items():
        if value is not None:
            merged[key] = value
    return merged

"""Matrix authentication — register, login, and credential persistence.

Handles the Synapse registration flow (dummy auth), login via matrix-nio,
and saving/loading access tokens for reuse across restarts.

v0.1.0
"""

import json
import os

import aiohttp
from nio import AsyncClient, LoginResponse

from .config import get_credentials_dir, ensure_config_dirs


async def register(homeserver: str, username: str, password: str) -> dict:
    """Register a new Matrix account via the client API.

    Synapse requires a two-step flow:
    1. POST register with username/password -> get session ID
    2. POST register again with auth dict including session ID

    Returns the registration response dict on success.
    Raises RuntimeError on failure.
    """
    url = f"{homeserver.rstrip('/')}/_matrix/client/v3/register"
    payload = {
        "username": username,
        "password": password,
        "kind": "user",
    }

    async with aiohttp.ClientSession() as session:
        # Step 1: initial request to get session
        async with session.post(url, json=payload) as resp:
            data = await resp.json()

            if resp.status == 200:
                # Server accepted without auth (unlikely but possible)
                return data

            if resp.status != 401:
                error = data.get("error", "Unknown error")
                raise RuntimeError(f"Registration failed: {error}")

            # Step 2: complete with dummy auth
            session_id = data.get("session")
            if not session_id:
                raise RuntimeError("Registration failed: no session in 401 response")

            payload["auth"] = {
                "type": "m.login.dummy",
                "session": session_id,
            }

            async with session.post(url, json=payload) as resp2:
                data2 = await resp2.json()

                if resp2.status == 200:
                    return data2

                error = data2.get("error", "Unknown error")
                raise RuntimeError(f"Registration failed: {error}")


async def login(client: AsyncClient, password: str) -> LoginResponse:
    """Login to Matrix and return the response.

    Raises RuntimeError if login fails.
    """
    resp = await client.login(password)
    if not isinstance(resp, LoginResponse):
        raise RuntimeError(f"Login failed: {resp}")
    return resp


def save_credentials(username: str, user_id: str, access_token: str,
                     device_id: str, homeserver: str):
    """Save credentials to disk for reuse."""
    ensure_config_dirs()
    path = os.path.join(get_credentials_dir(), f"{username}.json")
    data = {
        "user_id": user_id,
        "access_token": access_token,
        "device_id": device_id,
        "homeserver": homeserver,
    }
    with open(path, "w") as f:
        json.dump(data, f, indent=2)
    os.chmod(path, 0o600)


def load_credentials(username: str) -> dict | None:
    """Load saved credentials from disk. Returns None if not found."""
    path = os.path.join(get_credentials_dir(), f"{username}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)

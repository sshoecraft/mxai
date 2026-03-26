# credentials.py — Matrix Auth

## Purpose
Handles Matrix account registration, login, and credential persistence so bots can restart without re-registering.

## Registration flow
Synapse uses a two-step registration:
1. POST `/_matrix/client/v3/register` with username/password → 401 with session ID
2. POST again with `auth: {type: "m.login.dummy", session: <id>}` → 200 with access token

If the user already exists, `register()` raises RuntimeError with "User ID already taken" — caller can catch this and fall through to login.

## Credential storage
- Path: `~/.config/mxai/credentials/{username}.json`
- Contents: `{user_id, access_token, device_id, homeserver}`
- Permissions: 0600 (user-read-only)
- Bot checks for saved creds on startup → if found, skips registration and login entirely

## History
- v0.1.0 (2026-03-25): Initial implementation with aiohttp for registration, matrix-nio for login.

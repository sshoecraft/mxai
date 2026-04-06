"""MXAI — bridges a Matrix account to an AI adapter subprocess.

One MXAI = one Matrix user + one AI backend process.
Messages from Matrix rooms are forwarded to the adapter; adapter
responses are sent back to the originating room.

v0.2.2
"""

import asyncio
import json
import logging
import time
from urllib.parse import urlparse

# Suppress nio's noisy timeout/retry warnings
logging.getLogger("nio").setLevel(logging.ERROR)

from nio import (
    AsyncClient,
    InviteMemberEvent,
    LoginResponse,
    RoomMessageText,
)

from .adapters import get_adapter
from .credentials import register, login, save_credentials, load_credentials


class MXAI:
    """A single AI participant on Matrix."""

    def __init__(self, homeserver: str, name: str, backend: str,
                 system_prompt: str = "", username: str = None,
                 password: str = None, do_register: bool = False,
                 room: str = "General", verbose: bool = False,
                 debug: bool = False, extra_args: list = None):
        self.homeserver = homeserver
        self.name = name
        self.backend = backend
        self.system_prompt = system_prompt
        self.username = username or name
        self.password = password
        self.do_register = do_register
        self.room = room
        self.verbose = verbose or debug
        self.debug = debug
        self.extra_args = extra_args or []

        self.matrix_client = None
        self.adapter = None
        self.loop = None
        self._input_queue = None
        self._active_room_id = None
        self._adapter_done = None
        self._processing_task = None
        self._response_received = False

        self.start_time = None
        self.turn_count = 0
        self.total_cost = 0.0
        self.tool_count = 0
        self.login_timestamp = None

    async def start(self):
        """Authenticate, spawn adapter, and sync forever."""
        self.loop = asyncio.get_running_loop()
        self.start_time = time.time()

        self._input_queue = asyncio.Queue()
        self._adapter_done = asyncio.Event()
        self._adapter_done.set()

        await self._authenticate()
        self.server_name = self.matrix_client.user_id.split(":", 1)[1]
        self._setup_matrix_callbacks()
        self._spawn_adapter()

        self._processing_task = asyncio.create_task(self._process_input_queue())

        print(f"  [{self.name}] online as {self.matrix_client.user_id}", flush=True)
        print(f"  [{self.name}] backend: {self.backend}", flush=True)

        await self._auto_join_room()

        print(f"  [{self.name}] waiting for messages...", flush=True)

        await self.matrix_client.sync_forever(timeout=30000)

    async def stop(self):
        """Shut down the bot."""
        if self._processing_task:
            self._processing_task.cancel()
        if self.adapter:
            self.adapter.kill()
        if self.matrix_client:
            await self.matrix_client.close()

        uptime = time.time() - self.start_time if self.start_time else 0
        print(f"  [{self.name}] stopped. "
              f"turns={self.turn_count} cost=${self.total_cost:.2f} "
              f"uptime={int(uptime)}s", flush=True)

    # -- authentication --

    async def _authenticate(self):
        """Register (if requested) and login, using saved creds if available."""
        saved = load_credentials(self.username)

        if saved:
            print(f"  [{self.name}] using saved credentials", flush=True)
            self.matrix_client = AsyncClient(
                saved["homeserver"],
                saved["user_id"],
            )
            self.matrix_client.access_token = saved["access_token"]
            self.matrix_client.device_id = saved["device_id"]
            self.matrix_client.user_id = saved["user_id"]
            self.login_timestamp = time.time() * 1000
            return

        if self.do_register:
            pw = self.password or self.username
            print(f"  [{self.name}] registering @{self.username} on {self.homeserver}",
                  flush=True)
            try:
                reg_data = await register(self.homeserver, self.username, pw)
                print(f"  [{self.name}] registered successfully", flush=True)

                # Registration response includes access token — use it directly
                self.matrix_client = AsyncClient(
                    self.homeserver,
                    reg_data["user_id"],
                )
                self.matrix_client.access_token = reg_data["access_token"]
                self.matrix_client.device_id = reg_data["device_id"]
                self.matrix_client.user_id = reg_data["user_id"]

                save_credentials(
                    self.username,
                    reg_data["user_id"],
                    reg_data["access_token"],
                    reg_data["device_id"],
                    self.homeserver,
                )
                print(f"  [{self.name}] credentials saved", flush=True)
                self.login_timestamp = time.time() * 1000
                return

            except RuntimeError as e:
                if "User ID already taken" in str(e):
                    print(f"  [{self.name}] already registered, will login", flush=True)
                else:
                    raise
            self.password = pw

        if not self.password:
            raise RuntimeError(
                f"No saved credentials and no password provided for '{self.username}'. "
                "Use --register or --password."
            )

        server_name = urlparse(self.homeserver).hostname
        self.matrix_client = AsyncClient(
            self.homeserver,
            f"@{self.username}:{server_name}",
        )
        print(f"  [{self.name}] logging in as @{self.username}", flush=True)
        resp = await login(self.matrix_client, self.password)

        save_credentials(
            self.username,
            resp.user_id,
            resp.access_token,
            resp.device_id,
            self.homeserver,
        )
        print(f"  [{self.name}] credentials saved", flush=True)
        self.login_timestamp = time.time() * 1000

    async def _auto_join_room(self):
        """Join the configured room on startup."""
        room = self.room
        # If it's already a full alias or room ID, use as-is
        if not room.startswith("#") and not room.startswith("!"):
            room = f"#{room}:{self.server_name}"

        print(f"  [{self.name}] joining {room}...", flush=True)
        resp = await self.matrix_client.join(room)
        if hasattr(resp, "room_id"):
            print(f"  [{self.name}] joined {room} ({resp.room_id})", flush=True)
        else:
            print(f"  [{self.name}] failed to join {room}: {resp}", flush=True)

    # -- matrix callbacks --

    def _setup_matrix_callbacks(self):
        """Register event callbacks on the Matrix client."""
        self.matrix_client.add_event_callback(
            self._on_room_message, RoomMessageText)
        self.matrix_client.add_event_callback(
            self._on_invite, InviteMemberEvent)
    async def _process_input_queue(self):
        """Process queued messages serially — one at a time."""
        while True:
            room_id, msg = await self._input_queue.get()

            if self.debug:
                print(f"  [DEBUG {self.name}] processing: room={room_id} msg={msg[:200]}", flush=True)
                print(f"  [DEBUG {self.name}] queue depth: {self._input_queue.qsize()}", flush=True)

            self._active_room_id = room_id
            self._response_received = False
            self._adapter_done.clear()

            self.adapter.send(msg)

            if self.debug:
                print(f"  [DEBUG {self.name}] sent to adapter, waiting for response...", flush=True)

            await self._adapter_done.wait()
            self._active_room_id = None

            if self.debug:
                print(f"  [DEBUG {self.name}] adapter done, queue depth: {self._input_queue.qsize()}", flush=True)

    async def _on_room_message(self, room, event):
        """Handle incoming room messages."""
        # Skip our own messages
        if event.sender == self.matrix_client.user_id:
            if self.debug:
                print(f"  [DEBUG {self.name}] skipping own message", flush=True)
            return

        # Skip messages from before we logged in
        if self.login_timestamp and event.server_timestamp < self.login_timestamp:
            if self.debug:
                print(f"  [DEBUG {self.name}] skipping pre-login message (ts={event.server_timestamp} < login={self.login_timestamp})", flush=True)
            return

        sender_display = self._get_display_name(room, event.sender)
        msg = json.dumps({
            "room": room.display_name,
            "sender": sender_display,
            "mxid": event.sender,
            "message": event.body,
        })

        if self.verbose:
            print(f"  <<< [{room.display_name}] {sender_display}: {event.body[:120]}", flush=True)

        if self.debug:
            print(f"  [DEBUG {self.name}] queuing: room={room.display_name} active={self._active_room_id} queue_size={self._input_queue.qsize()}", flush=True)

        await self._input_queue.put((room.room_id, msg))

        if self.debug:
            print(f"  [DEBUG {self.name}] queued, queue_size={self._input_queue.qsize()}", flush=True)

    async def _on_invite(self, room, event):
        """Auto-join rooms we're invited to."""
        if event.membership == "invite" and event.state_key == self.matrix_client.user_id:
            if self.verbose:
                print(f"  [{self.name}] invited to {room.room_id}, joining...", flush=True)
            resp = await self.matrix_client.join(room.room_id)
            if hasattr(resp, "room_id"):
                if self.verbose:
                    print(f"  [{self.name}] joined {room.room_id}", flush=True)
            else:
                print(f"  [{self.name}] failed to join: {resp}", flush=True)

    # -- adapter --

    def _spawn_adapter(self):
        """Create and start the AI adapter subprocess."""
        system_prompt = self._build_system_prompt()
        self.adapter = get_adapter(self.backend, system_prompt,
                                   extra_args=self.extra_args,
                                   debug=self.debug)

        if self.debug:
            print(f"\n  === DEBUG [{self.name}] system prompt ===", flush=True)
            print(system_prompt, flush=True)
            print(f"  === END system prompt ===\n", flush=True)

            argv = self.adapter.build_command()
            print(f"  === DEBUG [{self.name}] adapter command ===", flush=True)
            print(f"  {' '.join(argv)}", flush=True)
            print(f"  === END adapter command ===\n", flush=True)

        self.adapter.on_response = self._on_adapter_response
        self.adapter.on_tool_use = self._on_adapter_tool_use
        self.adapter.on_result = self._on_adapter_result
        self.adapter.on_exit = self._on_adapter_exit

        self.adapter.spawn()
        print(f"  [{self.name}] adapter spawned ({self.backend})", flush=True)

    def _looks_like_impersonation(self, text: str) -> bool:
        """Check if text contains JSON payloads that impersonate another user."""
        for line in text.split("\n"):
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                obj = json.loads(stripped)
                if "sender" in obj and "message" in obj:
                    return True
            except (json.JSONDecodeError, TypeError):
                continue
        return False

    def _on_adapter_response(self, text: str):
        """Adapter produced text — intercept commands, send the rest as messages."""
        if self.debug:
            print(f"  [DEBUG {self.name}] on_response called, text_len={len(text)} active_room={self._active_room_id}", flush=True)
            print(f"  [DEBUG {self.name}] response text: {text[:300]}", flush=True)

        room_id = self._active_room_id
        if room_id is None:
            if self.debug:
                print(f"  [DEBUG {self.name}] WARNING: on_response but no active room — dropping", flush=True)
            return

        self._response_received = True

        # Block impersonation: if the response contains JSON message payloads,
        # reject it and tell the model to retry in its own voice.
        # Limit retries to prevent infinite correction loops.
        if self._looks_like_impersonation(text):
            self.impersonation_strikes = getattr(self, "impersonation_strikes", 0) + 1
            if self.impersonation_strikes <= 3:
                print(f"  [{self.name}] BLOCKED impersonation attempt ({self.impersonation_strikes}/3), sending correction", flush=True)
                self._response_received = False
                correction = json.dumps({
                    "sender": "system",
                    "mxid": "@system:localhost",
                    "message": (
                        "YOUR PREVIOUS RESPONSE WAS BLOCKED. You output a JSON object "
                        "with sender/message fields, which impersonates another user. "
                        "This is strictly forbidden. Respond again using your own voice "
                        "as plain text. If you need to reference what someone said, "
                        "use attribution like 'The Architect noted...'"
                    ),
                })
                self.adapter.send(correction)
                return
            else:
                # Give up after 3 strikes — strip the JSON lines and send what's left
                print(f"  [{self.name}] impersonation persists after 3 corrections, stripping JSON lines", flush=True)
                self.impersonation_strikes = 0
                cleaned = []
                for line in text.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("{"):
                        try:
                            obj = json.loads(stripped)
                            if "sender" in obj and "message" in obj:
                                continue
                        except (json.JSONDecodeError, TypeError):
                            pass
                    cleaned.append(line)
                text = "\n".join(cleaned)
        else:
            self.impersonation_strikes = 0

        # Split into lines, separate commands from text
        commands = []
        message_lines = []
        for line in text.split("\n"):
            stripped = line.strip()
            if stripped.startswith("/"):
                commands.append(stripped)
            else:
                message_lines.append(line)

        # Send any non-command text as a message
        message_text = "\n".join(message_lines).strip()

        asyncio.run_coroutine_threadsafe(
            self._handle_response(room_id, message_text, commands),
            self.loop,
        )

    async def _handle_response(self, room_id: str, message_text: str,
                               commands: list):
        """Send message text and execute any commands."""
        if self.debug:
            print(f"  [DEBUG {self.name}] _handle_response room={room_id} text_len={len(message_text)} cmds={len(commands)}", flush=True)

        if message_text:
            await self._send_matrix_message(room_id, message_text)
        elif self.debug:
            print(f"  [DEBUG {self.name}] no message text to send", flush=True)

        for cmd in commands:
            if self.debug:
                print(f"  [DEBUG {self.name}] executing command: {cmd[:120]}", flush=True)
            await self._execute_command(room_id, cmd)

    async def _execute_command(self, room_id: str, cmd: str):
        """Execute a Matrix command from the AI's response."""
        parts = cmd.split(None, 1)
        verb = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if verb == "/leave":
            if self.verbose:
                print(f"  [{self.name}] leaving {room_id}", flush=True)
            await self.matrix_client.room_leave(room_id)

        elif verb == "/join":
            room = arg
            if not room:
                return
            if not room.startswith("#") and not room.startswith("!"):
                room = f"#{room}:{self.server_name}"
            if self.verbose:
                print(f"  [{self.name}] joining {room}", flush=True)
            resp = await self.matrix_client.join(room)
            if hasattr(resp, "room_id"):
                if self.verbose:
                    print(f"  [{self.name}] joined {room} ({resp.room_id})", flush=True)
            else:
                print(f"  [{self.name}] failed to join {room}: {resp}", flush=True)

        elif verb == "/invite":
            user = arg
            if not user:
                return
            if not user.startswith("@"):
                user = f"@{user}:{self.server_name}"
            if self.verbose:
                print(f"  [{self.name}] inviting {user} to {room_id}", flush=True)
            await self.matrix_client.room_invite(room_id, user)

        elif verb == "/nick":
            if arg:
                if self.verbose:
                    print(f"  [{self.name}] setting display name to {arg}", flush=True)
                await self.matrix_client.set_displayname(arg)

        elif verb == "/topic":
            if arg:
                if self.verbose:
                    print(f"  [{self.name}] setting topic in {room_id}", flush=True)
                await self.matrix_client.room_put_state(
                    room_id, "m.room.topic", {"topic": arg})

        elif verb == "/room":
            if not arg:
                return
            alias = arg if arg.startswith("#") else f"#{arg}:{self.server_name}"
            localpart = arg.split(":")[0].lstrip("#")
            if self.verbose:
                print(f"  [{self.name}] creating room {alias}", flush=True)
            resp = await self.matrix_client.room_create(
                alias=localpart,
                name=localpart,
                visibility="private",
            )
            if hasattr(resp, "room_id"):
                if self.verbose:
                    print(f"  [{self.name}] created {alias} ({resp.room_id})", flush=True)
                join_resp = await self.matrix_client.join(resp.room_id)
                if self.verbose and hasattr(join_resp, "room_id"):
                    print(f"  [{self.name}] joined {alias}", flush=True)
            else:
                print(f"  [{self.name}] failed to create room {alias}: {resp}", flush=True)

        elif verb == "/msg":
            # /msg <user> <message> — send a direct message
            parts2 = arg.split(None, 1)
            if len(parts2) < 2:
                return
            user = parts2[0]
            message = parts2[1]
            if not user.startswith("@"):
                user = f"@{user}:{self.server_name}"
            if self.verbose:
                print(f"  [{self.name}] DM to {user}: {message[:80]}", flush=True)

            # Find existing DM room or create one
            dm_room_id = None
            for rid, room in self.matrix_client.rooms.items():
                if len(room.users) <= 2 and user in room.users:
                    dm_room_id = rid
                    break

            if not dm_room_id:
                resp = await self.matrix_client.room_create(
                    is_direct=True, invite=[user])
                if hasattr(resp, "room_id"):
                    dm_room_id = resp.room_id
                else:
                    print(f"  [{self.name}] failed to create DM: {resp}", flush=True)
                    return

            await self._send_matrix_message(dm_room_id, message)

        else:
            # Unknown command — just send it as text
            await self._send_matrix_message(room_id, cmd)

    def _on_adapter_tool_use(self, name: str, desc: str):
        """Adapter is using a tool."""
        if self.debug:
            print(f"  [DEBUG {self.name}] tool_use: {name} — {desc[:80]}", flush=True)
        self.tool_count += 1

    def _on_adapter_result(self, cost: float, turns: int):
        """Adapter turn completed — signal processing loop if response delivered.

        Only signals done when _response_received is True. Shepherd emits
        end_turn after tool-use rounds (no text) — those must not release
        the lock or the final text response will be orphaned.
        """
        if self.debug:
            print(f"  [DEBUG {self.name}] on_result: cost={cost} turns={turns} active={self._active_room_id} response_received={self._response_received}", flush=True)

        self.total_cost = cost
        self.turn_count += turns

        if self._response_received:
            self.loop.call_soon_threadsafe(self._adapter_done.set)
            if self.debug:
                print(f"  [DEBUG {self.name}] on_result: signaled adapter_done", flush=True)
        elif self.debug:
            print(f"  [DEBUG {self.name}] on_result: NOT signaling (no response yet)", flush=True)

    def _on_adapter_exit(self, exit_code: int):
        """Adapter process exited — respawn and notify rooms."""
        print(f"  [{self.name}] adapter exited (code {exit_code})", flush=True)
        self._active_room_id = None
        self._response_received = False
        # Drain input queue — messages during crash are lost
        while not self._input_queue.empty():
            try:
                self._input_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        # Unblock processing loop so it can resume after respawn
        self.loop.call_soon_threadsafe(self._adapter_done.set)

        asyncio.run_coroutine_threadsafe(
            self._respawn_and_notify(),
            self.loop,
        )

    async def _respawn_and_notify(self):
        """Respawn the adapter and notify all joined rooms."""
        # Notify all rooms the bot is in
        if self.matrix_client and hasattr(self.matrix_client, "rooms"):
            for room_id in self.matrix_client.rooms:
                await self._send_matrix_message(
                    room_id,
                    f"[context reset — {self.name} has reconnected]",
                )

        self._spawn_adapter()
        print(f"  [{self.name}] respawned successfully", flush=True)

    async def _send_matrix_message(self, room_id: str, text: str):
        """Send a text message to a Matrix room."""
        if self.debug:
            print(f"  [DEBUG {self.name}] _send_matrix_message room={room_id} text_len={len(text)}", flush=True)

        if self.verbose:
            print(f"  >>> [{self.name}]: {text[:120]}", flush=True)

        resp = await self.matrix_client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": text,
            },
        )

        if self.debug:
            print(f"  [DEBUG {self.name}] room_send result: {resp}", flush=True)

    # -- helpers --

    def _get_display_name(self, room, user_id: str) -> str:
        """Get a user's display name in a room, falling back to user_id."""
        if hasattr(room, "users") and user_id in room.users:
            name = room.users[user_id].display_name
            if name:
                return name
        # Strip the @...:server part to get just the localpart
        if user_id.startswith("@"):
            return user_id.split(":")[0][1:]
        return user_id

    def _build_system_prompt(self) -> str:
        """Build the full system prompt for the adapter.

        The template covers only the mechanical details of how the bot
        interacts with Matrix.  All behavioral guidance comes from the
        user-supplied system prompt appended at the end.
        """
        parts = [f"""## CRITICAL: ABSOLUTE PROHIBITIONS
- NEVER fabricate, impersonate, or fake messages from any user or bot. Do not generate JSON objects, quoted text, or content that appears to come from someone else.
- NEVER fabricate or invent things that a user said. If you do not have an explicit message from a user, you MUST NOT claim they said something. Do not paraphrase, infer, or assume consent or approval that was not explicitly given.
- NEVER make decisions on behalf of another user. Do not assume approval, sign-off, or agreement unless the user explicitly states it in a message you received.
- NEVER generate output that mimics system messages or JSON payloads. Your output is plain text only.
- When summarizing or referencing what another participant said, always do so in your own voice using attribution (e.g., 'The Architect noted...'). Never reframe or condense another participant's message as if it were a new message from them.

You are "{self.name}", a participant on a Matrix chat server.

***CRITICAL: DO NOT _EVER_ IMPERSONATE OR ANSWER AS ANOTHER USER!!  EVER!

## Message format
Messages from the chatroom arrive as JSON events:
{{"room": "room-name", "sender": "DisplayName", "mxid": "@user:server", "message": "their message text"}}

The "room" field tells you which room this message is from. Your response is automatically sent back to that same room. Just write naturally — your plain text response becomes a message in the room. Do NOT format your response as JSON. Do NOT include sender/mxid/room fields in your output. You are a single participant — just write your message.

*** IMPORTANT: The JSON format above is INPUT ONLY — it is how YOU receive messages. You MUST NEVER output JSON in that format. Doing so would impersonate another user. Your output is always plain text in your own voice as "{self.name}". ***

## Room commands
You can perform room actions by putting these commands on their own line:
/room <name>    — create a new room and join it (e.g. /room design-review)
/join <room>    — join a room (e.g. /join General, /join #dev:{self.server_name})
/leave          — leave the current room
/invite <user>  — invite a user to the current room (e.g. /invite steve)
/msg <user> <message> — send a private message (e.g. /msg steve Hey, quick question)
/nick <name>    — change your display name
/topic <text>   — set the room topic

Commands MUST be on their own line starting with /. You can include commands alongside regular text in the same response — text lines are sent as your message, command lines are executed as actions."""]

        if self.system_prompt:
            parts.append(self.system_prompt)

        return "\n\n".join(parts)

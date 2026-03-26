"""MatrixBot — bridges a Matrix account to an AI adapter subprocess.

One MatrixBot = one Matrix user + one AI backend process.
Messages from Matrix rooms are forwarded to the adapter; adapter
responses are sent back to the originating room.

v0.1.1
"""

import asyncio
import collections
import time

from nio import (
    AsyncClient,
    InviteMemberEvent,
    LoginResponse,
    RoomMessageText,
)

from .adapters import get_adapter
from .credentials import register, login, save_credentials, load_credentials


class MatrixBot:
    """A single AI participant on Matrix."""

    def __init__(self, homeserver: str, name: str, backend: str, role: str,
                 username: str = None, password: str = None,
                 do_register: bool = False, model: str = None,
                 effort: str = None, room: str = "Lobby"):
        self.homeserver = homeserver
        self.name = name
        self.backend = backend
        self.role = role
        self.username = username or name
        self.password = password
        self.do_register = do_register
        self.model = model
        self.effort = effort
        self.room = room

        self.matrix_client = None
        self.adapter = None
        self.loop = None
        self.response_queue = collections.deque()
        self.response_pending = False

        self.start_time = None
        self.turn_count = 0
        self.total_cost = 0.0
        self.tool_count = 0
        self.login_timestamp = None

    async def start(self):
        """Authenticate, spawn adapter, and sync forever."""
        self.loop = asyncio.get_running_loop()
        self.start_time = time.time()

        await self._authenticate()
        self._setup_matrix_callbacks()
        self._spawn_adapter()

        print(f"  [{self.name}] online as {self.matrix_client.user_id}", flush=True)
        print(f"  [{self.name}] backend: {self.backend}", flush=True)

        await self._auto_join_room()

        print(f"  [{self.name}] waiting for messages...", flush=True)

        await self.matrix_client.sync_forever(timeout=30000)

    async def stop(self):
        """Shut down the bot."""
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

        self.matrix_client = AsyncClient(
            self.homeserver,
            f"@{self.username}:localhost",
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
            room = f"#{room}:localhost"

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
    async def _on_room_message(self, room, event):
        """Handle incoming room messages."""
        # Skip our own messages
        if event.sender == self.matrix_client.user_id:
            return

        # Skip messages from before we logged in
        if self.login_timestamp and event.server_timestamp < self.login_timestamp:
            return

        sender_display = self._get_display_name(room, event.sender)
        formatted = f"[{sender_display}]: {event.body}"

        print(f"  <<< [{room.display_name}] {formatted[:120]}", flush=True)

        self.response_queue.append(room.room_id)
        self.adapter.send(formatted)

    async def _on_invite(self, room, event):
        """Auto-join rooms we're invited to."""
        if event.membership == "invite" and event.state_key == self.matrix_client.user_id:
            print(f"  [{self.name}] invited to {room.room_id}, joining...", flush=True)
            resp = await self.matrix_client.join(room.room_id)
            if hasattr(resp, "room_id"):
                print(f"  [{self.name}] joined {room.room_id}", flush=True)
            else:
                print(f"  [{self.name}] failed to join: {resp}", flush=True)

    # -- adapter --

    def _spawn_adapter(self):
        """Create and start the AI adapter subprocess."""
        system_prompt = self._build_system_prompt()
        self.adapter = get_adapter(self.backend, system_prompt,
                                   model=self.model, effort=self.effort)

        self.adapter.on_response = self._on_adapter_response
        self.adapter.on_tool_use = self._on_adapter_tool_use
        self.adapter.on_result = self._on_adapter_result
        self.adapter.on_exit = self._on_adapter_exit

        self.adapter.spawn()
        print(f"  [{self.name}] adapter spawned ({self.backend})", flush=True)

    def _on_adapter_response(self, text: str):
        """Adapter produced text — intercept commands, send the rest as messages."""
        if not self.response_queue:
            return

        room_id = self.response_queue.popleft()
        self.response_pending = False

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

    SILENCE_MARKERS = {
        "(no response)", "(silence)", "(no reply)", "(none)",
        "[no response]", "[silence]", "[no reply]", "[none]",
        "…", "...",
    }

    async def _handle_response(self, room_id: str, message_text: str,
                               commands: list):
        """Send message text and execute any commands."""
        if message_text and message_text.strip().lower() not in self.SILENCE_MARKERS:
            await self._send_matrix_message(room_id, message_text)

        for cmd in commands:
            await self._execute_command(room_id, cmd)

    async def _execute_command(self, room_id: str, cmd: str):
        """Execute a Matrix command from the AI's response."""
        parts = cmd.split(None, 1)
        verb = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if verb == "/leave":
            print(f"  [{self.name}] leaving {room_id}", flush=True)
            await self.matrix_client.room_leave(room_id)

        elif verb == "/join":
            room = arg
            if not room:
                return
            if not room.startswith("#") and not room.startswith("!"):
                room = f"#{room}:localhost"
            print(f"  [{self.name}] joining {room}", flush=True)
            resp = await self.matrix_client.join(room)
            if hasattr(resp, "room_id"):
                print(f"  [{self.name}] joined {room} ({resp.room_id})", flush=True)
            else:
                print(f"  [{self.name}] failed to join {room}: {resp}", flush=True)

        elif verb == "/invite":
            user = arg
            if not user:
                return
            if not user.startswith("@"):
                user = f"@{user}:localhost"
            print(f"  [{self.name}] inviting {user} to {room_id}", flush=True)
            await self.matrix_client.room_invite(room_id, user)

        elif verb == "/nick":
            if arg:
                print(f"  [{self.name}] setting display name to {arg}", flush=True)
                await self.matrix_client.set_displayname(arg)

        elif verb == "/topic":
            if arg:
                print(f"  [{self.name}] setting topic in {room_id}", flush=True)
                await self.matrix_client.room_put_state(
                    room_id, "m.room.topic", {"topic": arg})

        elif verb == "/msg":
            # /msg <user> <message> — send a direct message
            parts2 = arg.split(None, 1)
            if len(parts2) < 2:
                return
            user = parts2[0]
            message = parts2[1]
            if not user.startswith("@"):
                user = f"@{user}:localhost"
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
        self.tool_count += 1

    def _on_adapter_result(self, cost: float, turns: int):
        """Adapter turn completed."""
        self.total_cost = cost
        self.turn_count += turns

        # If no response was sent this turn, pop the queue to stay in sync
        if self.response_queue and not self.response_pending:
            self.response_queue.popleft()
        self.response_pending = False

    def _on_adapter_exit(self):
        """Adapter process exited — respawn and notify rooms."""
        print(f"  [{self.name}] adapter process exited, respawning...", flush=True)
        self.response_queue.clear()

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
        print(f"  >>> [{self.name}]: {text[:120]}", flush=True)

        await self.matrix_client.room_send(
            room_id=room_id,
            message_type="m.room.message",
            content={
                "msgtype": "m.text",
                "body": text,
            },
        )

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
        """Build the full system prompt for the adapter."""
        return f"""You are "{self.name}", a participant in a Matrix chat server.

## Environment
You are connected to a Matrix homeserver as a regular user. You exist in chat rooms alongside humans and other AI agents. You interact exactly like any other Matrix user — you can join rooms, leave rooms, invite people, send messages, and use your tools to do real work.

## How this works
Messages from the chatroom arrive as:
[DisplayName]: message text

Your response is sent to the room automatically. Just write naturally — your text becomes a message in the room.

## Your role
{self.role}

## Room commands
You can perform room actions by putting these commands on their own line:
/join <room>    — join a room (e.g. /join Lobby, /join #dev:localhost)
/leave          — leave the current room
/invite <user>  — invite a user to the current room (e.g. /invite steve)
/msg <user> <message> — send a private message (e.g. /msg steve Hey, quick question)
/nick <name>    — change your display name
/topic <text>   — set the room topic

Commands MUST be on their own line starting with /. You can include commands alongside regular text in the same response — text lines are sent as your message, command lines are executed as actions. For example, to leave a room, say goodbye and put /leave on its own line.

## Tool use — ALWAYS narrate what you're doing
You have full tool access (reading files, writing files, running commands, searching the web, etc.). USE your tools when asked to do work.
CRITICAL: Before using ANY tool, you MUST announce what you're about to do in your response text. The people in this chatroom cannot see your tool calls — they only see your text messages. If you silently use tools without saying anything, it looks like you went quiet for no reason.

Examples of correct behavior:
- "Let me read that file." (then use the Read tool)
- "I'll search for that now." (then use WebSearch)
- "Running that command to check." (then use Bash)
- "Writing the updated code to disk." (then use Write)

After the tool completes, report what you found or what happened. The chatroom is your only way to communicate results — no one sees your tool output directly.

## Rules
- Stay in character for your role at all times
- Address others by name when responding to them
- Keep responses focused and concise
- CRITICAL: Do NOT respond when you have nothing meaningful to add. No "sounds good", no "agreed", no thumbs up, no "standing by", no acknowledgments. Silence is fine. Only speak when you have NEW information, a question, a decision, or actionable content. If someone says something that doesn't require your input, say nothing — do not reply at all.
- Do NOT repeat what others have already said
- Do NOT end messages with offers like "let me know if you need anything"
"""

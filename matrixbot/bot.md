# bot.py — MatrixBot

## Purpose
Core class that bridges a Matrix user account to an AI adapter subprocess. One MatrixBot = one Matrix user + one AI backend process.

## Architecture

```
Matrix (Synapse)  ←→  MatrixBot  ←→  Adapter subprocess (Claude/Shepherd)
       nio.AsyncClient     │           threading + subprocess.Popen
                           │
                     response_queue (deque of room_ids)
```

### Message flow
1. Matrix message arrives via `sync_forever()` → `_on_room_message()` callback
2. Message formatted as `[DisplayName]: text`, room_id pushed to `response_queue`
3. `adapter.send(formatted)` writes to subprocess stdin
4. Adapter stdout thread parses response → `on_response` callback fires
5. Room_id popped from queue, `asyncio.run_coroutine_threadsafe()` sends to Matrix

### Thread/async bridge
- Main loop: asyncio (matrix-nio's `sync_forever`)
- Adapter I/O: threads (see `adapters/base.py`)
- Adapter callbacks → asyncio via `run_coroutine_threadsafe()`

### Room management
- Auto-joins on invite via `InviteMemberEvent` callback
- Skips own messages (sender == user_id)
- Skips messages from before login (server_timestamp < login_timestamp)

## Key state
- `response_queue`: deque of room_ids waiting for adapter responses
- `login_timestamp`: ms timestamp used to filter old messages on initial sync
- `total_cost`, `turn_count`, `tool_count`: stats from adapter

## History
- v0.1.0 (2026-03-25): Initial implementation. Replaced old/ MQTT-based Instance class.

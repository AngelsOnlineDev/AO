# World Server Flow

## Overview

After the login server redirects the client, the world server handles all gameplay.

```
Phase 1:   S->C  Hello (new XOR key)
Phase 2:   C->S  Auth (session token) + optional ACK
Phase 2b:  C->S  ACK (0x0003, if not bundled with auth)
Phase 3:   S->C  Init packets (2 compressed packets)
Phase 3b:  S->C  ACK response (64 bytes)
Phase 3c:  S->C  Skill data (compressed)
Phase 3d:  S->C  Area entity packets (NPCs, monsters)
Phase 4:   Both  Game loop (keepalives + gameplay packets)
```

## Phase 1: Hello

Same format as login server Hello. New random 16-byte key generated for this connection.

**Dual Crypto Contexts:**
- `CryptXOR(key)` for S->C (server to client) — static key
- `CryptXORIV(key)` for C->S (client to server) — evolving key

## Phase 2: Auth

Client sends auth packet containing the session token from the redirect.

```
Auth payload (37 bytes observed):
  [LE16 sub_length = 35]
  [LE16 sub_type = 0x0002]
  [... auth data ...]
  [4B session_token]           # Last 4 bytes of payload
```

### Token Resolution

```python
from game_server import consume_session
token = auth_payload[-4:]
entity_id = consume_session(token)  # Pops from _pending_sessions

if entity_id:
    player = database.get_player(entity_id)
else:
    player = None

# Fallback: use first player in DB (for debugging)
if player is None:
    player = db.execute("SELECT * FROM players LIMIT 1").fetchone()
```

### Bundled ACK

The client often sends auth + ACK (0x0003) in the same TCP segment. The server checks for this:

```python
early_packets = await self._receive_packets(reader, framer, crypto_recv, addr, timeout=5.0)
auth_payload = early_packets[0]
for pkt in early_packets[1:]:
    if opcode_at_offset_2(pkt) == 0x0003:
        got_ack = True
```

## Phase 2b: Wait for ACK

If the ACK wasn't bundled with auth, wait for it separately (30s timeout).

```
ACK packet (4+ bytes):
  [LE16 sub_length]
  [LE16 opcode = 0x0003]
```

## Game Data Loading

After auth is accepted, the server loads game data for the player's zone:

```python
map_data = get_map(player['map_id'])              # Map from PAK files
npc_db = get_npc_db()                              # 827 NPC definitions (npc.xml)
monster_db = get_monster_db()                      # 1682 monster definitions (monster.xml)
entity_registry = dict(get_seed_entity_registry()) # Entity ID -> NPC type mappings
dialog_manager = get_dialog_manager()              # 42k strings, 24k dialog nodes
```

**Performance Note**: This loading takes ~7 seconds on slow machines. These should be cached at startup.

## Phase 3: Init Packets

Two large LZO-compressed packets containing the player's world state.

### Init Packet 1 (init_pkt1.hex, ~36KB decompressed)

Contains entity references, map anchors, timestamps, and entity settings. Per-player patches:

| Opcode | Size | Patched Field |
|--------|------|---------------|
| 0x0185 | 14B | `entity_id` (player's entity) |
| 0x0021 | 24B | `map_id` (player's current zone) |
| 0x005D | 6B | `timestamp` (current Unix time) |

### Init Packet 2 (init_pkt2.hex, ~5KB decompressed)

Contains character stats and currency. Per-player patches:

| Opcode | Size | Patched Field |
|--------|------|---------------|
| 0x0042 | 107B | `hp`, `hp_max`, `mp`, `mp_max` |
| 0x0149 | 38B | `gold` (currency amount) |

### Template Patching

Templates are loaded once from `tools/seed_data/*.hex`, decompressed, then patched per-player using `_find_sub_message()` to locate opcodes within the sub-message stream.

## Phase 3b: ACK Response (64 bytes)

Sent in response to the client's ACK. Contains 6 sub-messages:

```
[LE16 len][0x0142 toggle_flag (3B)]     x2
[LE16 len][0x001D entity_setting (16B)]  x1  (with player's entity_id)
[LE16 len][0x0027 area_ref (10B)]        x1
[LE16 len][0x0040 area_ref (10B)]        x1
[LE16 len][0x003F area_ref (10B)]        x1
```

## Phase 3c: Skill Data

34 skill slot sub-messages (opcode 0x0158, 74 bytes each), LZO-compressed.

Loaded from `tools/seed_data/init_pkt4.hex`. Currently not per-player patched.

## Phase 3d: Area Entity Packets

NPC and monster spawn packets for the player's current zone.

**Sources** (priority order):
1. **Map-based**: If map PAK data is available, generate spawns from map entities
2. **Seed data**: Fallback to pre-captured hex files (area_pkt01-17.hex)

Each area packet contains sub-messages:
- `0x0008` (65B) — NPC spawn (name, position, sprite, type)
- `0x0007` (7B) — Entity position marker
- `0x000E` (45B) — Static entity spawn
- `0x000F` (46B) — Monster spawn

## Phase 4: Game Loop

Two concurrent tasks run after init:

### Keepalive Loop

```python
while True:
    tick_count += 1
    if tick_count % 60 == 0:
        minute_count += 1
        send(keepalive_timer(minute_count))  # 0x018A, 14 bytes
    else:
        send(keepalive_tick())                # 0x018A, 10 bytes
    await asyncio.sleep(1.0)
```

**Tick** (every 1s): `[0x018A][LE32 type=4][LE32 data=0]` (10 bytes)
**Timer** (every 60s): `[0x018A][LE32 type=8][LE32 minute][LE32 0]` (14 bytes)

### Packet Dispatch

The game loop reads packets and dispatches by opcode:

```python
while True:
    data = await reader.read(65536)
    packets = framer.feed(data)
    for hdr, raw in packets:
        payload = decrypt_and_trim(raw)
        opcode = LE16(payload[2:4])

        # Priority 1: ACK (inline)
        if opcode == 0x0003:
            send_ack_response(entity_id)

        # Priority 2: Entity action opcodes
        elif opcode in (0x0005, 0x0019):
            await npc.handle_entity_action(...)

        # Priority 3: Dispatch table
        elif opcode in OPCODE_HANDLERS:
            await OPCODE_HANDLERS[opcode](...)

        # Priority 4: Silent/logging opcodes
        else:
            log_unknown_opcode(opcode, payload)
```

## Opcode Dispatch Table

```python
OPCODE_HANDLERS = {
    0x0004: movement.handle_movement,     # Walk/run
    0x0006: misc.handle_entity_select,    # Click entity
    0x0009: combat.handle_stop_action,    # Cancel action
    0x000D: npc.handle_entity_action,     # Click NPC
    0x000F: combat.handle_target_mob,     # Target monster
    0x0012: misc.handle_buy_sell,         # Shop transaction
    0x0016: combat.handle_use_skill,      # Cast skill
    0x001A: social.handle_request_player_details,
    0x002E: social.handle_chat_send,      # Chat message
    0x003E: misc.handle_toggle_action,    # Sit/stand/meditate
    0x0044: npc.handle_npc_dialog,        # Dialog option select
    0x0143: misc.handle_zone_ready,       # Zone load complete
    0x0150: social.handle_emote,          # Emote animation
}
```

### Special Inline Opcodes

| Opcode | Handling |
|--------|----------|
| 0x0003 | ACK — send 64-byte ACK response |
| 0x0005, 0x0019 | Entity action — route to `npc.handle_entity_action` with length check |
| 0x0034 | Cancel action — clear dialog state |

### Silent Opcodes (logged, no response)

| Opcode | Name |
|--------|------|
| 0x0007 | ENTITY_POS_ACK |
| 0x000B | ENTITY_STATUS_ACK |
| 0x000E | ENTITY_SPAWN_ACK |
| 0x015E | PING |
| 0x0152 | ANTI_AFK_TICK |

## Zone Transfer

When a player warps to another map (via NPC gate or dialog action):

```python
async def handle_zone_transfer(server, writer, builder, session,
                                dest_map_id, spawn_point, addr):
    1. Resolve spawn position from config.MAP_SPAWN_POINTS
    2. Load new map: get_map(dest_map_id)
    3. Load NPC/monster databases
    4. Merge map's local dialogs
    5. Create fresh entity_registry
    6. Generate area packets for new zone
    7. Send area packets to client
    8. Send movement response (new position)
    9. Update session state (map_id, pos, map_data, registry)
    10. Persist to database
```

## Broadcasting

The world server can broadcast packets to all players in a zone:

```python
async def broadcast_to_zone(self, map_id, payload, exclude_entity=0):
    for session in self.tracker.get_zone_sessions(map_id, exclude_entity):
        pkt = session['builder'].build_packet(payload)
        session['writer'].write(pkt)
        await session['writer'].drain()
```

## Connection Cleanup

On disconnect (normal or error):

```python
finally:
    writer.close()
    session = self.sessions.pop(str(addr), None)
    if session:
        self.tracker.unregister(session.get('entity_id', 0))
    log.info(f"[{addr}] Session ended")
```

## Error Handling

| Error | Handling |
|-------|----------|
| ConnectionReset/BrokenPipe | Info log, cleanup |
| IncompleteReadError | Info log, cleanup |
| Windows errors (64, 10053, 10054) | Treated as disconnect |
| Other OSError | Full traceback logged |
| Handler exceptions | Caught at top level, logged |

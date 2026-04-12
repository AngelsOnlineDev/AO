# World server

Port `27901`. Everything that happens after the player commits to entering the game lives here. Source: [world_server.py](../src/world_server.py).

```
Phase 1   S→C  Hello (new 16B XOR key)
Phase 2   C→S  Auth (session token) — may bundle C→S ACK 0x0003
Phase 3   S→C  Init packets 1 + 2 (compressed), then S→C ACK response
Phase 4   C→S  C→S ACK (0x0003) if not bundled
Phase 5   S→C  Skill data (compressed, init_pkt4)
Phase 6   S→C  Area entity packets (NPCs, monsters)
Phase 7   both Game loop: keepalives + bidirectional dispatch
```

## Phase 1 — Hello ✅

Identical format to the login-server Hello (see [03_LOGIN_FLOW.md](03_LOGIN_FLOW.md)), just with a new random key. The session's `CryptXOR` (S→C, stateless) and `CryptXORIV` (C→S, evolving) are both seeded from this key.

## Phase 2 — Auth ✅

First encrypted packet. Sub-message opcode `0x0002`, containing the 4-byte session token from Phase 5 of the login flow:

```
[LE16 sub_len][LE16 0x0002][… 29B …][4B session_token]
```

The token is the last 4 bytes of the payload. Server calls [`consume_session(token)`](../src/game_server.py) (defined in the login server) to resolve it to an `entity_id`.

❓ The 29 bytes between the opcode and the token are replayed-only; we don't know what they represent. Likely a client build hash or timestamp. Never varies in captures.

If the client bundles auth + ACK into the same TCP segment, the server picks up both at once via `_receive_packets(..., timeout=5.0)`. Otherwise it waits for the ACK in Phase 4 separately.

**Session map pin** ⚠: [world_server.py:221](../src/world_server.py#L221) currently does:
```python
map_id = 129  # TODO: honor player['map_id']
```
regardless of the DB's value. The captured init packets are for map 129 so we can only "start" there. Zone transfers at runtime still work.

## Phase 3 — Init packets ✅

Two LZO-compressed packets, built by [world_init_builder.py](../src/world_init_builder.py) from captured templates in `tools/seed_data/`.

### init_pkt1 (~36 KB decompressed)

Contains map geometry, entity anchors, timestamps, NPC spawns for the starting area, and the **player profile** (`0x0002` sub-message).

Per-player patches:

| Sub-op | Field | What we write |
|---|---|---|
| `0x0185` | `entity_id` (player) | session's entity_id |
| `0x0021` | `map_id` | 129 (pinned) |
| `0x005D` | `timestamp` | current Unix time |
| `0x0002` | **player profile** | name, class, level, appearance, HP/MP, stats (see [world_init_builder.py:194](../src/world_init_builder.py#L194)) |

See [09_REVERSE_ENGINEERING.md](09_REVERSE_ENGINEERING.md) for the full `0x0002` byte layout including the faction-vs-class-id saga.

### init_pkt2 (~5 KB decompressed)

Character sheet: HP/MP, currency, stamina.

| Sub-op | Field |
|---|---|
| `0x0042` | hp, hp_max, mp, mp_max, stats |
| `0x0149` | gold |

### init_pkt4 (skills, ~2.5 KB decompressed)

34 × `0x0158` skill slots. ❓ Currently replayed verbatim from the capture — per-player skill loadouts are not patched yet.

## Phase 3b — S→C ACK response ✅

Right after the init packets, the server sends a 64-byte payload with six sub-messages:

```
0x0142  toggle flag       3B   (x2)
0x001D  entity setting    16B  (carries player entity_id)
0x0027  area ref          10B
0x0040  area ref          10B
0x003F  area ref          10B
```

The client expects this before it starts sending gameplay packets. ❓ The three area-ref sub-messages look like "clear any cached area state for these IDs" pings; meanings of the individual `0x0027 / 0x003F / 0x0040` opcodes are not confirmed beyond "shut up the client".

## Phase 6 — Area entity packets ✅

NPC and monster spawns for the current map. Sourced by [area_entity_data.py](../src/area_entity_data.py):

1. If a loaded `MapData` is present, spawns are regenerated dynamically from it.
2. Otherwise fall back to the pre-captured `area_pkt01.hex`..`area_pkt17.hex` seeds.

Each area packet is compressed and contains sub-messages:

| Sub-op | Size | Meaning |
|---|---|---|
| `0x0008` | 65B | NPC spawn (name, position, sprite, type) |
| `0x0007` | 7B | Entity position marker |
| `0x000E` | 45B | Static entity spawn |
| `0x000F` | 46B | Monster spawn |

During loading, the server builds a **runtime entity registry** (`entity_id → npc_type_id`) so later clicks on an NPC can be resolved back to an xml definition. This registry is rebuilt from scratch on every zone transfer.

## Phase 7 — Game loop ✅

Two concurrent tasks:

### Keepalive loop

```python
while connected:
    if tick_count % 60 == 0:
        send(0x018A keepalive_timer 14B)   # every 60s
    else:
        send(0x018A keepalive_tick 10B)    # every 1s
    tick_count += 1
    await asyncio.sleep(1.0)
```

If we skip keepalives for more than ~3s the client disconnects. Never had a case where it was sensitive to exact timing within that budget.

### Opcode dispatch

Single reader pulling packets via the `PacketFramer`; each payload's first sub-message opcode is looked up in `OPCODE_HANDLERS` (see [world_server.py:54](../src/world_server.py#L54)). The real dispatch table as of now:

```python
OPCODE_HANDLERS = {
    0x0004: movement.handle_movement,              # MOVEMENT_REQ
    0x0006: misc.handle_entity_select,             # ENTITY_SELECT
    0x0009: combat.handle_stop_action,             # STOP_ACTION
    0x000d: npc.handle_entity_action,              # ENTITY_ACTION (NPC click)
    0x000f: combat.handle_target_mob,              # TARGET_MOB
    0x0012: misc.handle_buy_sell,                  # BUY_SELL
    0x0016: combat.handle_use_skill,               # USE_SKILL
    0x001a: social.handle_request_player_details,  # REQUEST_PLAYER_DETAILS
    0x002e: social.handle_chat_send,               # CHAT_SEND
    0x003e: misc.handle_toggle_action,             # TOGGLE_ACTION
    0x0044: npc.handle_npc_dialog,                 # NPC_DIALOG
    0x0143: misc.handle_zone_ready,                # ZONE_READY
    0x0150: social.handle_emote,                   # EMOTE
}
```

Inline specials (handled outside the table):

- `0x0003` → send S→C ACK response
- `0x0005`, `0x0019` → forwarded to `npc.handle_entity_action` (they share payload shape with `0x000d`)

Unknown opcodes just get `log.debug`'d; there's no fallback handler. A small allow-list of known no-ops (`0x0027`, `0x0101`, `0x0122`, `0x0127`, `0x012b`, `0x012d`, `0x0139`) is silenced to keep logs clean.

## Multiplayer presence

Three wire events get broadcast across sessions on the same `map_id`. All of this lives in [presence.py](../src/presence.py), wrapping the `PlayerTracker` so handlers don't iterate sessions themselves.

| Event | Triggered by | Packets sent to observers |
|---|---|---|
| Player joins zone | Phase 3 completes | `0x0001` + `0x000E` spawn (dual — see below) |
| Player moves | [movement.handle_movement](../src/handlers/movement.py) after echoing MOVE_RESP to the mover | `0x0005` ENTITY_MOVE |
| Player leaves | Session teardown, before `tracker.unregister` | `0x001B` ENTITY_DESPAWN |
| Player enters a zone that already has others | Same as "joins" | The newcomer also receives spawn packets for every existing session, so they see everyone immediately |

### Dual-opcode spawn ✅

On join, every spawn broadcast sends **two** sub-messages: `0x0001` **and** `0x000E`.

- `0x0001` (client handler `sub_5E97F0`) has the rich layout — name, appearance bytes, guild, equipment slots — but it bails if the receiver isn't in a char-select / fresh-login state. It reliably lands on clients that haven't finished their init flow yet.
- `0x000E` (client handler `sub_5EF410`) is a bare-bones spawn (entity id, tile position, sprite template 999). It works mid-game but carries no name or appearance.

Sending both lets each receiver process whichever one its current state accepts; the unused opcode is ignored. This is how we get two already-in-world clients to see each other.

**Current bug** ❌: `0x0001` carries 8 + 3 equipment slot IDs (offsets 66-97 and 98/102/108). We send all zeros because our DB has no `equipment` table yet, so remote players render in the default naked model regardless of what their owning client sees. Fix path: add an `equipment` table and populate these slots before calling `build_remote_player_spawn`. See `presence._spawn_subs` in [presence.py](../src/presence.py) for the call site.

## Mob state

[mob_state.py](../src/mob_state.py) holds a `MobRegistry`: one `Mob` per `entity_id` tracking current/max HP and a respawn deadline. Mobs are lazily registered the first time a player damages one (combat handler), not pre-populated from the map.

The world server runs a `_mob_tick_loop` task ([world_server.py](../src/world_server.py#L89)) every 5s that calls `tracker.tick_respawns()` — any dead mob past its `RESPAWN_DELAY_SEC` (30s) has its HP restored. ❓ We don't yet broadcast a respawn packet, so clients that saw the death animation won't see the mob reappear until they zone out and back.

## Zone transfer

When an NPC dialog action warps the player ([movement.handle_zone_transfer](../src/handlers/movement.py)):

1. Resolve target `(x, y)` from `config.MAP_SPAWN_POINTS[(dest_map_id, spawn_point)]`, falling back to `DEFAULT_SPAWN`.
2. Load `MapData` for the destination map.
3. Rebuild the runtime entity registry and regenerate area entity packets.
4. Merge the destination map's local dialogs into the session's dialog manager.
5. Send the area packets.
6. Send a movement response at the new position so the client updates its world coords.
7. Persist `map_id / pos_x / pos_y` to the DB via `update_player_map`.
8. Presence broadcasts aren't currently re-run on zone-transfer — ❓ so other players on the new map don't see the arriving player until the next movement tick. Fix pending.

## Broadcasting helper

```python
async def broadcast_to_zone(self, map_id, payload, exclude_entity=0):
    for session in self.tracker.get_zone_sessions(map_id, exclude_entity):
        pkt = session['builder'].build_packet(payload)
        session['writer'].write(pkt)
        await session['writer'].drain()
```

Used by chat. Presence broadcasts use a lower-level path with `gather()` drains so one slow socket doesn't block the others.

## Cleanup + error handling

On any disconnect the `finally` block:

1. Calls `presence.broadcast_despawn` while the session is still in the tracker.
2. `tracker.unregister(entity_id)` to remove from the zone index.
3. Pops from `self.sessions`.
4. Closes the writer.

| Error | Behavior |
|---|---|
| `ConnectionResetError`, `BrokenPipeError`, `IncompleteReadError` | info-level log, clean cleanup |
| Windows 64 / 10053 / 10054 | treated as disconnect |
| Anything else | full traceback |

## Known unknowns

- ❌ **The 29 mystery bytes of the auth packet.** Replayed verbatim; we don't know the layout.
- ❌ **How the client expects a respawn announcement**. We don't send one — need to check what the real server sent after mob death.
- ❓ **The `0x0027 / 0x003F / 0x0040 / 0x001D` area-ref sub-messages in the ACK response.** Needed for the client to proceed but the per-byte meaning is guessed.
- ❌ **Walkability.** The server doesn't know which tiles are passable — movement is trusted from the client. Fix requires parsing map `.MPC` collision data from the PAK archives.
- ❌ **Skill data per-player patching.** `init_pkt4` is replayed as-is; each player gets Soualz's skill loadout.

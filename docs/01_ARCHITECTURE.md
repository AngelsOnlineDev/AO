# Architecture

## Processes

One Python process, multiple async TCP servers started from [server.py](../src/server.py) via `asyncio.gather()`:

```
server.py
  ├─ LoginServer   (game_server.py)   :16768  auth, slot mgmt, redirect
  ├─ WorldServer   (world_server.py)  :27901  gameplay
  ├─ FileServer    (file_server.py)   :21238  avatar portraits (stub, no responses yet)
  ├─ PatchServer   (patch_server.py)  :80     HTTP stub for Start.exe update check
  └─ FtpServer     (ftp_server.py)    :21     FTP stub for Start.exe version check
```

The Patch and FTP servers exist only so the original launcher finishes its update flow. They require admin/port-80 privileges; if you bypass the launcher, you can skip them.

## Startup sequence

1. [game_finder.py](../src/game_finder.py) locates the AO install dir (saved in `ao_config.ini`, or prompted).
2. [database.py](../src/database.py) opens `data/angels.db`, runs schema migrations, enables WAL mode.
3. Each server's `.start()` opens its listener.
4. `asyncio.gather(...)` runs them until shutdown.

## Module map

### Core
| File | Purpose |
|---|---|
| [server.py](../src/server.py) | Entry point, logging, orchestration |
| [config.py](../src/config.py) | All tunables + env var overrides |
| [game_server.py](../src/game_server.py) | Login server loop, phase-4 slot ops, login response builder |
| [world_server.py](../src/world_server.py) | World loop, opcode dispatch, session state, mob tick loop |
| [file_server.py](../src/file_server.py) | Avatar portrait server (stub) |
| [patch_server.py](../src/patch_server.py) | HTTP stub for `GET /patch.php` |
| [ftp_server.py](../src/ftp_server.py) | FTP stub serving `version.ini` |

### Protocol
| File | Purpose |
|---|---|
| [packet.py](../src/packet.py) | Header encode/decode, `PacketBuilder`, framer, reader |
| [crypto.py](../src/crypto.py) | `CryptXOR` (S→C stateless) + `CryptXORIV` (C→S stateful) |
| [packet_builders.py](../src/packet_builders.py) | All S→C builders; `pack_sub()`; sub-message framing |

### Game logic
| File | Purpose |
|---|---|
| [handlers/movement.py](../src/handlers/movement.py) | `MOVEMENT_REQ`, zone transfer, dialog-action warps |
| [handlers/npc.py](../src/handlers/npc.py) | NPC interact, dialog state, Census Angel, shop stubs |
| [handlers/combat.py](../src/handlers/combat.py) | `USE_SKILL`, `TARGET_MOB`, auto-attack on mob click |
| [handlers/social.py](../src/handlers/social.py) | Chat, emote, player detail request |
| [handlers/misc.py](../src/handlers/misc.py) | Entity select, buy/sell stub, toggle sit/stand, zone ready |
| [presence.py](../src/presence.py) | Multiplayer broadcast: spawn/despawn/movement to zone observers |
| [class_stats.py](../src/class_stats.py) | Per-class base stats + level scaling |
| [mob_state.py](../src/mob_state.py) | Mob HP registry + respawn timer |

### Data
| File | Purpose |
|---|---|
| [database.py](../src/database.py) | SQLite schema, account/char CRUD, position/HP updates |
| [game_data.py](../src/game_data.py) | Lazy singletons: NPC DB, monster DB, dialog manager, quest manager |
| [map_loader.py](../src/map_loader.py) | Parses `.MPC` maps when PAK extraction is available |
| [area_entity_data.py](../src/area_entity_data.py) | Seed hex loading, runtime entity registry |
| [world_init_builder.py](../src/world_init_builder.py) | Per-player init packet patching |
| [player_tracker.py](../src/player_tracker.py) | `entity_id → session` + `map_id → set[session]` index |
| [dialog_manager.py](../src/dialog_manager.py) | `msg.xml` / `spmsg.xml` / `EVENT.XML` dialog graph |

## Session dict

Each connected client gets a dict tracked in `WorldServer.sessions[addr]` and registered with the `PlayerTracker`:

```python
session = {
    # net
    'crypto':      CryptXOR,
    'crypto_recv': CryptXORIV,
    'builder':     PacketBuilder,
    'writer':      StreamWriter,
    # identity
    'entity_id':   int,
    'player_name': str,
    'player':      sqlite3.Row,   # full DB row (class_id, app0..4, hp, ...)
    # position
    'pos_x':       int,           # pixels (tile * 32)
    'pos_y':       int,
    'map_id':      int,           # pinned to 129 right now — see world_server.py:221
    # game data
    'map_data':    MapData | None,
    'npc_db':      dict,
    'monster_db':  dict,
    'entity_registry': dict,      # runtime_entity_id → npc_type_id
    # state
    'dialog_state':  DialogState | None,
    'player_quests': dict,
}
```

**Heads up**: [world_server.py:221](../src/world_server.py#L221) currently hardcodes `map_id = 129` regardless of what the DB says, because the captured seed packets are for map 129 and we don't yet generate init packets from scratch. Zone transfers still work at runtime but you can't *start* on another map.

## Multiplayer broadcast

The `PlayerTracker` keeps a `map_id → set[session]` index. [presence.py](../src/presence.py) wraps it with the three things a gameplay event needs to broadcast:

- `send_existing_players_to(new_session, tracker)` — on join, tell the newcomer about every player already on their map.
- `broadcast_spawn(new_session, tracker)` — tell everyone already on the map about the newcomer.
- `broadcast_movement(session, tracker, cur_x, cur_y, dst_x, dst_y, speed)` — relay the move response.
- `broadcast_despawn(session, tracker)` — tell observers when someone leaves.

See [04_WORLD_SERVER.md](04_WORLD_SERVER.md) for the dual-opcode detail (why spawn broadcasts send both `0x0001` and `0x000E`).

## Dependencies

- Python 3.10+ (`X | Y` type hints)
- `lzallright` — LZO for init packet decompression
- `pyftpdlib` — stub FTP server
- Everything else from stdlib (`asyncio`, `sqlite3`, `hashlib`, `struct`)

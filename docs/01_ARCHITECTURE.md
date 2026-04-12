# Architecture Overview

## Server Components

The server is a single Python process running three async TCP servers via `asyncio`:

```
server.py (entry point)
    |
    +-- LoginServer    (src/game_server.py)   port 16768
    |     Handles login, authentication, account creation, redirect
    |
    +-- WorldServer    (src/world_server.py)  port 27901
    |     Handles game world: init, movement, NPC, combat, chat
    |
    +-- FileServer     (src/file_server.py)   port 21238
          Asset/file server (currently stub, TODO)
```

## Startup Sequence

```python
# server.py::main()
1. game_finder.ensure_game_dir()     # Locate AO install (env/registry/GUI)
2. config.GAME_DIR = game_dir        # Store for map loading
3. LoginServer(host, port, ...)      # Create login server
4. WorldServer(host, port)           # Create world server
5. FileServer(host, port)            # Create file server
6. database.init()                   # Initialize SQLite (WAL mode)
7. asyncio.gather(                   # Run all three concurrently
       login.start(),
       world.start(),
       file.start(),
   )
```

## Source File Map

### Core Server
| File | Purpose |
|------|---------|
| `src/server.py` | Entry point, logging setup, starts all servers |
| `src/config.py` | All configuration (network, game, paths) |
| `src/game_server.py` | Login server + login response builder |
| `src/world_server.py` | World server + game loop + opcode dispatch |
| `src/file_server.py` | File server (stub) |

### Protocol Layer
| File | Purpose |
|------|---------|
| `src/packet.py` | Header encode/decode, PacketBuilder, PacketFramer, PacketReader/Writer |
| `src/crypto.py` | CryptXOR (S->C static key), CryptXORIV (C->S evolving key) |
| `src/packet_builders.py` | All S->C packet builders (50+ opcodes), `pack_sub()`, `assemble_payload()` |

### Game Logic
| File | Purpose |
|------|---------|
| `src/handlers/movement.py` | Movement, zone transfer, dialog action processing |
| `src/handlers/npc.py` | NPC interaction, dialog state machine, Census Angel, shops, gates |
| `src/handlers/combat.py` | Target mob, use skill, stop action |
| `src/handlers/social.py` | Chat, emotes, player details |
| `src/handlers/misc.py` | Entity select, buy/sell, toggle sit/stand, zone ready |

### Data Layer
| File | Purpose |
|------|---------|
| `src/database.py` | SQLite schema, account/character CRUD, position/stats updates |
| `src/game_data.py` | Lazy-loaded singletons: DialogManager, NPC DB, Monster DB, Quest Manager |
| `src/map_loader.py` | MPC map parsing from PAK archives, NPC/monster/event extraction |
| `src/area_entity_data.py` | Seed hex file loading, entity registry, area packet generation |
| `src/world_init_builder.py` | Dynamic init packet building with per-player patching |
| `src/world_init_data.py` | Legacy static init packet loading (fallback) |
| `src/player_tracker.py` | Zone-based player tracking for broadcasting |
| `src/game_finder.py` | AO install directory detection (env, registry, GUI) |

### Data Files
| Path | Purpose |
|------|---------|
| `data/angels.db` | SQLite database (accounts, players, entities) |
| `data/game_xml/` | Extracted XML (msg.xml, spmsg.xml, npc.xml, monster.xml, quest.xml) |
| `data/game_xml/setting/EVENT.XML` | Global dialog definitions |
| `tools/seed_data/` | Hex-encoded seed packets (init, area, skill data) |
| `logs/server.log` | Server log output |

## Configuration (config.py)

All settings can be overridden with environment variables:

| Setting | Default | Env Var | Description |
|---------|---------|---------|-------------|
| `HOST` | `127.0.0.1` | `AO_HOST` | Listen address |
| `REDIRECT_HOST` | Same as HOST | `AO_REDIRECT_HOST` | Address sent to client in redirect |
| `LOGIN_PORT` | `16768` | `AO_LOGIN_PORT` | Login server port |
| `WORLD_PORT` | `27901` | `AO_WORLD_PORT` | World server port |
| `FILE_PORT` | `21238` | `AO_FILE_PORT` | File server port |
| `KEEPALIVE_INTERVAL` | `1.0` | - | Seconds between keepalive packets |
| `MOVE_SPEED` | `110` | - | Character movement speed (0x6E) |
| `MAX_MOVE_STEP` | `200` | - | Max pixels per movement segment |
| `START_MAP_ID` | `2` | - | Default map (Angel Lyceum / Eden) |
| `DEFAULT_SPAWN` | `(1040, 720)` | - | Fallback spawn position |
| `GAME_DIR` | `C:\Program Files (x86)\Angels Online` | `AO_GAME_DIR` | Game install dir |
| `GAME_XML_DIR` | `data/game_xml/` | - | Extracted XML directory |

## Session Lifecycle

Each client connection creates a session dict stored in `WorldServer.sessions`:

```python
session = {
    # Network
    'crypto': CryptXOR,            # S->C encryption
    'crypto_recv': CryptXORIV,     # C->S decryption (evolving key)
    'builder': PacketBuilder,      # Packet construction
    'writer': StreamWriter,        # TCP socket

    # Player Identity
    'entity_id': int,              # 32-bit unique entity ID
    'player_name': str,            # Character display name

    # Position
    'pos_x': int,                  # Current X (tile-pixels)
    'pos_y': int,                  # Current Y (tile-pixels)
    'map_id': int,                 # Current map/zone ID

    # Game Data (loaded per-connection, should be cached)
    'map_data': MapData | None,    # Map geometry + events
    'npc_db': dict,                # NPC type definitions
    'monster_db': dict,            # Monster definitions
    'entity_registry': dict,       # runtime_entity_id -> npc_type_id

    # State
    'dialog_state': DialogState | None,  # Active NPC dialog
    'player_quests': dict,               # Quest tracking
}
```

## Multi-Player Support

The `PlayerTracker` manages connected players by zone:

```python
tracker = PlayerTracker()
tracker.register(entity_id, map_id, session)    # On connect
tracker.change_map(entity_id, new_map_id)        # On zone transfer
tracker.unregister(entity_id)                    # On disconnect

# Broadcasting to zone
for session in tracker.get_zone_sessions(map_id, exclude_entity=sender):
    send_packet(session, packet_data)
```

## Dependencies

- **Python 3.10+** (type hints with `X | Y` syntax)
- **lzallright** - LZO compression/decompression
- **asyncio** - Async TCP servers
- **sqlite3** - Database (stdlib)
- **hashlib** - Password hashing (stdlib)
- **struct** - Binary packing (stdlib)

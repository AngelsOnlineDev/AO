# Architecture Overview

This document describes how the Angels Online private server is structured, how data flows from client connection to gameplay, and how the modules relate to each other.

---

## Server Components

The server consists of three TCP servers running concurrently via Python's `asyncio`:

```
┌─────────────┐     ┌──────────────┐     ┌──────────────┐
│ Login Server │     │ World Server │     │  File Server  │
│  port 16768  │     │  port 27901  │     │  port 21238   │
│              │────>│              │     │    (stub)      │
│ game_server  │     │ world_server │     │  file_server   │
│    .py       │     │    .py       │     │    .py         │
└─────────────┘     └──────────────┘     └──────────────┘
      │                    │
      │                    ├── handlers/movement.py
      │                    ├── handlers/npc.py
      │                    ├── handlers/combat.py
      │                    ├── handlers/social.py
      │                    └── handlers/misc.py
      │
      └── Sends session token + redirect to world server
```

### Login Server (`game_server.py`)

Handles authentication and redirects the client to the world server.

**Flow:**
1. Client connects → server sends **Hello** with 16-byte XOR key
2. Client sends **Login Request** with username
3. Server looks up or creates player in database
4. Server sends **Login Response** (compressed, contains MOTD + account data)
5. Server sends **Redirect** with session token + world server IP:port
6. Client disconnects and reconnects to world server

### World Server (`world_server.py`)

Handles all gameplay. Uses a **dispatch table** pattern — a dict mapping opcodes to handler functions:

```python
OPCODE_HANDLERS = {
    0x0003: _handle_ack,        # Connection init
    0x0004: movement.handle,     # Movement
    0x000D: npc.handle_action,   # NPC interaction
    0x002E: social.handle_chat,  # Chat
    ...
}
```

When a packet arrives, the server:
1. Decrypts and deframes the packet
2. Extracts the opcode from the first sub-message
3. Looks up the handler in the dispatch table
4. Calls the handler with `(writer, session, payload, builder, crypto)`

### File Server (`file_server.py`)

Stub — the client connects to verify assets but no protocol is implemented yet.

---

## Module Dependency Graph

```
server.py (entry point)
├── config.py (settings, env vars, spawn points)
├── database.py (SQLite: players table, init_db)
├── game_finder.py (locate Angels Online install dir)
├── game_server.py (Login Server)
│   ├── crypto.py (CryptXOR, CryptXORIV)
│   ├── packet.py (PacketFramer, PacketBuilder)
│   └── database.py
├── world_server.py (World Server)
│   ├── crypto.py
│   ├── packet.py
│   ├── packet_builders.py (44+ opcode builders)
│   ├── game_data.py (shared singletons)
│   │   ├── dialog_manager.py (dialog trees from XML)
│   │   ├── map_loader.py (MPC map parser)
│   │   ├── quest_manager.py (quest state)
│   │   └── area_entity_data.py (NPC/entity spawn packets)
│   ├── world_init_builder.py (dynamic init packet assembly)
│   ├── player_tracker.py (zone-based player tracking)
│   └── handlers/
│       ├── movement.py (movement, zone transfers)
│       ├── npc.py (NPC interaction, dialog, behaviors)
│       ├── combat.py (targeting, skills)
│       ├── social.py (chat, emotes)
│       └── misc.py (entity select, shop, toggles)
└── file_server.py (File Server, stub)
```

---

## Data Flow: Client Connection to Gameplay

```
CLIENT                    LOGIN SERVER              WORLD SERVER
  │                           │                          │
  │── TCP connect ──────────>│                          │
  │<── Hello (XOR key) ─────│                          │
  │── Login Request ────────>│                          │
  │                          │── DB lookup/create ──>   │
  │<── Login Response ───────│   (database.py)          │
  │<── Redirect ─────────────│                          │
  │── TCP disconnect ────────│                          │
  │                                                     │
  │── TCP connect ─────────────────────────────────────>│
  │<── Hello (XOR key) ────────────────────────────────│
  │── ACK (0x0003) ────────────────────────────────────>│
  │                                                     │── Build init packets
  │                                                     │   (world_init_builder.py)
  │<── Init Packet 1 (char definition, compressed) ────│
  │<── Init Packet 2 (entity data, compressed) ────────│
  │<── Init Packet 3 (stats, hotbar) ──────────────────│
  │<── Init Packet 4 (slots, config, zones) ───────────│
  │<── Area Packets x17 (NPCs, mobs, objects) ────────│
  │                                                     │
  │── ZONE_READY (0x0143) ────────────────────────────>│
  │<── Keepalive tick ─────────────────────────────────│
  │                                                     │
  │══ Gameplay loop ═══════════════════════════════════│
  │── Movement (0x0004) ──────────────────────────────>│
  │<── Position update (0x0005) ───────────────────────│
  │── NPC Click (0x000D) ─────────────────────────────>│
  │<── Chat/Dialog response ───────────────────────────│
  │── Chat (0x002E) ──────────────────────────────────>│
  │<── Chat broadcast (0x001E) ────────────────────────│
```

---

## Packet Processing Pipeline

```
Raw TCP bytes
    │
    ▼
PacketFramer.feed_data()     ← Buffers incoming bytes
    │
    ▼
Header deobfuscation         ← XOR with 0x1357, sequence XOR, flag XOR
    │
    ▼
CryptXORIV.decrypt()         ← 16-byte evolving key (C->S)
    │
    ▼
Checksum validation          ← 0xD31F seed, XOR words, rotate
    │
    ▼
LZO decompress (if flag)    ← lzallright library
    │
    ▼
Sub-message parsing          ← [LE16 length][data] repeated
    │
    ▼
Opcode extraction            ← First LE16 of first sub-message
    │
    ▼
Dispatch to handler          ← OPCODE_HANDLERS[opcode]
    │
    ▼
Handler builds response      ← packet_builders.py functions
    │
    ▼
PacketBuilder.build_packet() ← Frame + encrypt + checksum
    │
    ▼
CryptXOR.encrypt()           ← 16-byte static key (S->C)
    │
    ▼
writer.write() + drain()     ← Send to client
```

---

## Session State

Each connected client has a `session` dict that persists for the connection lifetime:

```python
session = {
    'entity_id':     0x543809CC,  # Player's entity ID
    'player':        {...},        # Player DB row (name, level, class, hp, mp, gold)
    'current_map':   2,            # Current map ID
    'pos_x':         1040,         # Current position
    'pos_y':         720,
    'dialog_state':  None,         # Active NPC dialog tree state
    'session_token': b'\x...',     # Auth token from login server
}
```

---

## Shared Game Data (`game_data.py`)

Singleton objects loaded once at startup and shared across all handlers:

| Singleton | Source | Purpose |
|-----------|--------|---------|
| `dialog_mgr` | `game_xml/*.xml` | NPC dialog tree traversal |
| `map_data` | `.MPC` files in PAK | Map entities, triggers, warp points |
| `quest_mgr` | `game_xml/quest.xml` | Quest definitions and state |
| `area_data` | `area_entity_data.py` | Pre-built NPC/entity spawn packets |
| `entity_registry` | Built at runtime | Maps runtime entity ID → NPC type ID |
| `npc_names` | `game_xml/npc.xml` | NPC type ID → display name |

---

## Zone Transfer Flow

When a player warps to a new map (via gate NPC or dialog action):

```
1. Handler determines target map_id and spawn_point
2. Look up spawn coordinates from config.MAP_SPAWN_POINTS
3. Update session: current_map, pos_x, pos_y
4. Update database: player's map_id, x, y
5. Build new area entity packets for target map
6. Send area packets to client (NPCs, mobs, objects for new zone)
7. Client receives entities and renders the new map
```

---

## Adding a New Opcode Handler

1. Identify the opcode from client packet captures
2. Create or update the handler function in `handlers/`:
   ```python
   async def handle_new_opcode(writer, session, payload, builder, crypto):
       # Parse payload
       # Build response
       # Send response
   ```
3. Register it in `world_server.py`'s dispatch table:
   ```python
   OPCODE_HANDLERS[0xNNNN] = handler_module.handle_new_opcode
   ```
4. If new S->C packets are needed, add builders to `packet_builders.py`

# Changelog

All notable changes to the Angels Online private server.

---

## [0.2.0] - 2026-03-20

### Architecture Refactor

- **Split world server monolith** — Extracted `world_server.py` (1386 lines) into modular components:
  - `game_data.py` — Shared singletons (dialog manager, map data, quest manager, entity registry)
  - `handlers/movement.py` — Movement and zone transfers
  - `handlers/npc.py` — NPC interaction, dialog trees, hardcoded behaviors
  - `handlers/combat.py` — Targeting and skill use
  - `handlers/social.py` — Chat and emotes
  - `handlers/misc.py` — Entity select, shop, toggles, zone ready
- **Dispatch table** — Replaced 40+ opcode `if/elif` chain with a dict-based dispatch system
- `world_server.py` reduced to 433 lines (packet dispatch + init sequence)

### Dynamic Init Packets

- **New `world_init_builder.py`** — Patches player-specific data into init packet templates instead of sending raw hex blobs
  - Entity ID, HP/MP, gold, map ID, timestamp patched dynamically
  - Templates lazy-loaded from `tools/seed_data/` hex files
- **Database schema expanded** — Added `hp`, `hp_max`, `mp`, `mp_max`, `gold` columns to players table

### Multi-Player Foundation

- **New `player_tracker.py`** — Zone-based spatial indexing for connected players
- **Session token persistence** — Login server generates and stores auth tokens
- **Token-based player lookup** — World server identifies players by session token (fallback to first player)
- **`broadcast_to_zone()` utility** — Send packets to all players in a zone
- **Configurable `AO_REDIRECT_HOST`** — Separate listen address from redirect address (for Wine, VM, LAN setups)

### Documentation

- Added `docs/protocol.md` — Complete packet protocol reference (780 lines)
- Added `docs/architecture.md` — Server architecture and data flow diagrams
- Added `docs/game-data.md` — Map IDs, class IDs, NPC types, quest list, constants
- Added `docs/contributing.md` — Guide for adding handlers, NPCs, warps, and decoding opcodes
- Added `README.md` — Setup instructions and project overview

---

## [0.1.0] - 2026-02-21

### Initial Release

Core server emulator with three TCP servers.

#### Login Server
- XOR key exchange handshake (16-byte key, Hello packet)
- Username-based authentication (accepts any credentials)
- LZO-compressed login response with MOTD and account data
- Session redirect to world server

#### World Server
- Full packet pipeline: deobfuscation, decryption (evolving XOR), decompression, sub-message framing
- 44+ packet builders in `packet_builders.py`
- Character initialization: 4 init packets + 17 area entity packets from captured hex data
- Movement with position validation and speed capping
- NPC interaction via entity registry lookup
- Dialog system with 703 global dialogs from `msg.xml`
- Zone warp via gate NPCs (map 2 → map 3)
- Chat system (local and NPC speech via 0x001E)
- Combat stubs: targeting, skill use, entity status
- Keepalive system: 1s ticks + 60s timer broadcasts
- Toggle actions: sit, stand, meditate

#### File Server
- Stub implementation (accepts connections, no protocol)

#### Map Loader
- Parses MPC binary map files from PAK archives
- Extracts entity positions, trigger zones, action definitions
- Supports dialog triggers (type 25) and warp triggers (type 37)

#### Dialog Manager
- Loads dialog trees from game XML files
- Supports global dialogs (`msg.xml`) and per-map local dialogs
- Dialog state tracking per session
- Action dispatch for warp (type 37) and dialog chain (type 25)

#### Quest Manager
- Loads quest definitions from `quest.xml`
- Step tracking with objective parsing
- Quest offer/accept/complete flow (basic)

#### Tools
- `game_sniffer.py` — Live packet capture and decode (requires scapy + Npcap)
- `pcap_analyzer.py` — Offline PCAP analysis
- `verify_builders.py` — Packet builder verification
- `seed_data/` — Captured init and area packets as hex files

#### Data
- SQLite database with players table
- Game XML files: NPC definitions, quests, monsters, dialogs, messages, settings
- Pre-configured `SERVER_PROXY.XML`

---

## Implementation Status

### Working
- Login handshake and authentication
- Character loading and world entry
- Movement with position sync
- NPC interaction and dialog trees
- Zone warp (gate NPCs + dialog actions)
- Local chat
- Entity spawning (NPCs, mobs, objects)
- Keepalive / connection maintenance
- Toggle actions (sit/stand/meditate)

### Stubbed (partially implemented)
- Combat (targeting works, damage is placeholder)
- Shops (NPC click recognized, buy/sell not wired)
- Skills (opcode handled, no effect applied)
- Emotes (opcode handled, no broadcast)
- Player inspection
- Quests (loaded but progression incomplete)

### Not Implemented
- Inventory system
- Equipment / gear
- Leveling / experience
- Party system
- Guild system
- Pet system
- PvP
- Mail system
- Auction house
- Instance dungeons
- File server protocol
- Native dialog windows (using chat fallback)

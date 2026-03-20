# Contributing Guide

This guide explains how to extend the server — adding new opcode handlers, NPC behaviors, zone warps, and decoding unknown packets.

---

## Table of Contents

1. [Development Setup](#development-setup)
2. [Capturing Packets](#capturing-packets)
3. [Decoding an Unknown Opcode](#decoding-an-unknown-opcode)
4. [Adding a New C->S Handler](#adding-a-new-cs-handler)
5. [Adding a New S->C Packet Builder](#adding-a-new-sc-packet-builder)
6. [Adding NPC Behaviors](#adding-npc-behaviors)
7. [Adding Zone Warps](#adding-zone-warps)
8. [Adding Maps](#adding-maps)
9. [Working with the Dialog System](#working-with-the-dialog-system)
10. [Database Changes](#database-changes)

---

## Development Setup

### Requirements

- Python 3.10+
- Angels Online client (installed, with PAK files)
- `pip install lzallright`
- (Optional) `pip install scapy` + Npcap for packet sniffing

### Running the Server

```bash
python src/server.py
```

The server auto-detects the game directory. Set `AO_GAME_DIR` if it's not in the default location. See `config.py` for all environment variables.

### Project Layout

```
src/
├── server.py              # Entry point
├── config.py              # All configurable constants
├── world_server.py        # Dispatch table (register handlers here)
├── packet_builders.py     # S->C packet builders (add new packets here)
├── handlers/              # C->S packet handlers
│   ├── movement.py
│   ├── npc.py
│   ├── combat.py
│   ├── social.py
│   └── misc.py
└── game_data.py           # Shared singletons
```

---

## Capturing Packets

### Using the Built-in Sniffer

The `tools/game_sniffer.py` tool captures live traffic between the client and a real server (or this server):

```bash
pip install scapy
python tools/game_sniffer.py
```

On Windows, you'll also need [Npcap](https://npcap.com/) installed.

### Using Wireshark

1. Capture on the loopback interface (or your network interface)
2. Filter by port: `tcp.port == 16768 || tcp.port == 27901`
3. Follow TCP streams to see the full packet exchange
4. Export as hex dump for analysis

### Using the PCAP Analyzer

```bash
python tools/pcap_analyzer.py capture.pcap
```

Decodes captured packets using the server's crypto and framing logic.

---

## Decoding an Unknown Opcode

When the server logs an unknown opcode:

```
[world_server] DEBUG: Unknown opcode 0x0048, payload (12 bytes): 48 00 ...
```

### Step-by-step

1. **Collect samples** — Trigger the same action multiple times and compare payloads
2. **Identify fixed vs variable bytes** — Bytes that never change are likely flags/constants
3. **Look for entity IDs** — 4-byte values matching known entity IDs (check entity_registry)
4. **Look for coordinates** — Pairs of LE16 or LE32 values that change with position
5. **Check the game XML** — The opcode number sometimes appears in XML config files
6. **Compare with known opcodes** — Similar-sized packets often have similar layouts

### Common patterns

| Pattern | Likely meaning |
|---------|---------------|
| Bytes 4-7 = known entity ID | Entity-targeted action |
| Bytes 4-5, 6-7 = small numbers | Item ID + quantity, or skill ID + level |
| Bytes 4-7 = large number, 8-11 = large number | Coordinate pair (x, y) |
| Single byte at offset 4 | Toggle flag or action type |
| Payload exactly matches another opcode's size | Variant of that opcode |

---

## Adding a New C->S Handler

### 1. Write the handler function

Create or update a file in `src/handlers/`. All handlers have the same signature:

```python
async def handle_my_opcode(writer, session, payload, builder, crypto):
    """Handle MY_OPCODE (0xNNNN) - description."""
    if len(payload) < 8:
        return

    # Parse the payload
    import struct
    target_id = struct.unpack_from('<I', payload, 4)[0]

    # Do game logic
    # ...

    # Build and send response
    response = build_my_response(target_id)
    packet = builder.build_packet(response)
    writer.write(packet)
    await writer.drain()
```

### 2. Register in the dispatch table

In `src/world_server.py`, add the import and register:

```python
from handlers import my_module

OPCODE_HANDLERS[0xNNNN] = my_module.handle_my_opcode
```

### 3. Test

1. Start the server
2. Trigger the action in the client
3. Check logs for the handler being called
4. Verify the client response

---

## Adding a New S->C Packet Builder

In `src/packet_builders.py`, add a function following the existing pattern:

```python
def build_my_packet(entity_id, value):
    """Build MY_PACKET (0xNNNN) - 14 bytes."""
    data = bytearray(14)
    struct.pack_into('<H', data, 0, 0xNNNN)   # opcode
    struct.pack_into('<I', data, 2, entity_id) # entity ID
    struct.pack_into('<I', data, 6, value)     # some value
    # ... fill remaining bytes
    return bytes(data)
```

### Framing

Individual sub-messages are concatenated and wrapped with length prefixes by `build_packet()`. If you need to send multiple sub-messages in one packet:

```python
payload = b''
payload += struct.pack('<H', len(msg1)) + msg1
payload += struct.pack('<H', len(msg2)) + msg2
packet = builder.build_packet(payload)
```

---

## Adding NPC Behaviors

### Hardcoded behaviors

In `src/handlers/npc.py`, add to the `NPC_BEHAVIORS` dict:

```python
NPC_BEHAVIORS = {
    # ... existing entries ...
    1234: {'type': 'shop', 'shop_id': 2},              # Opens shop
    5678: {'type': 'gate', 'map_id': 10, 'spawn': 0},  # Zone warp
    9999: {'type': 'quest_npc', 'quest_id': 200, 'dialog_id': 5},
    7777: {'type': 'totem'},                            # Teleporter
}
```

### Behavior types

| Type | Required fields | What it does |
|------|----------------|--------------|
| `shop` | `shop_id` | Opens shop (currently sends chat message) |
| `gate` | `map_id`, `spawn` | Triggers zone transfer |
| `quest_npc` | `quest_id`, `dialog_id` | Starts quest dialog |
| `totem` | — | Sends teleporter message |

### Dialog-based behaviors

If an NPC doesn't have a hardcoded behavior, the server falls back to the dialog system. To assign dialogs, add entries to the game XML files or configure them in `map_loader.py`.

---

## Adding Zone Warps

### 1. Add spawn points

In `src/config.py`, add entries to `MAP_SPAWN_POINTS`:

```python
MAP_SPAWN_POINTS = {
    # ... existing entries ...
    (10, 0): (300, 400),   # Map 10, spawn point 0
    (10, 1): (800, 200),   # Map 10, spawn point 1
}
```

### 2. Add gate NPC behavior

In `src/handlers/npc.py`:

```python
NPC_BEHAVIORS[1234] = {'type': 'gate', 'map_id': 10, 'spawn': 0}
```

### 3. Add area entity data (optional)

If the target map needs NPCs/mobs spawned, add entity data in `src/area_entity_data.py` or ensure the map's MPC file is loadable.

---

## Adding Maps

### From MPC files

The server can parse `.MPC` map files from the game's PAK archives. `map_loader.py` extracts:

- Entity positions (NPCs, objects)
- Trigger zones (talk, collision)
- Action definitions (dialog start, zone warp)

### Manually

If no MPC file is available, you can manually define map entities in `area_entity_data.py` using the packet builder functions.

---

## Working with the Dialog System

### Dialog sources

- **Global dialogs**: `data/game_xml/msg.xml` (703 dialogs, shared across maps)
- **Local dialogs**: `data/game_xml/map002_dialogs.xml` (per-map dialogs)
- **Event triggers**: `data/game_xml/map002_events.xml` (per-map event mappings)

### Dialog actions

Each dialog option can trigger an action:

| Action Type | Params | Effect |
|-------------|--------|--------|
| 37 | map_id, spawn_point, flag | Zone warp |
| 25 | dialog_id | Start another dialog |
| (others) | — | Not yet implemented |

### Adding a dialog

1. Add the dialog XML to the appropriate file in `data/game_xml/`
2. Follow the existing XML structure (see `msg.xml` for examples)
3. The dialog manager loads all dialogs at startup automatically

---

## Database Changes

### Schema

The database (`data/angels.db`) uses SQLite. Schema is defined in `src/database.py` in the `init_db()` function.

### Adding a column

1. Add the column to the `CREATE TABLE` statement in `database.py`
2. Add a migration `ALTER TABLE` for existing databases:
   ```python
   try:
       conn.execute("ALTER TABLE players ADD COLUMN new_field INTEGER DEFAULT 0")
   except:
       pass  # Column already exists
   ```
3. Update any queries that read/write the new column

### Seed data

The default player ("Player", level 29, class 6) is created by `init_db()` if the database is empty. Update the `INSERT` statement there to change defaults.

---

## Code Style

- Async everywhere — all handlers are `async def`
- Struct for binary — use `struct.pack`/`unpack` for all packet construction
- Hex in logs — log opcodes and entity IDs in hex (`0x{val:04X}`)
- Handler signature: `async def handle(writer, session, payload, builder, crypto)`
- Keep handlers focused — one opcode per function, shared logic in `game_data.py`

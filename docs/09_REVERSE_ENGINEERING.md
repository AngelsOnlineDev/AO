# Reverse Engineering Notes

Documentation of how the protocol was reverse-engineered, what's been discovered, and what remains unknown.

## Methodology

### Data Sources

| Source | Tool | What It Revealed |
|--------|------|------------------|
| Network captures (`.pcap`) | Wireshark/tshark | Packet structure, flow timing, opcodes |
| Game client binary (`Angel.exe`) | Ghidra | Dispatch tables, struct layouts, function signatures |
| Seed data (init packets) | Python scripts | Sub-message formats, field offsets |
| Live gameplay captures | Custom Python decoders | Opcode meanings, field mappings |

### Key Captures

| File | Content |
|------|---------|
| `Gameplay.pcap` | Full login + gameplay session |
| `GamePackets1/2.pcap` | World server packet captures |
| `walk_capture.pcap` | Movement packet analysis |
| `WalkAndError.pcap` | Movement with error conditions |
| `straight_walk.pcap` | Simple movement baseline |

## Protocol Discovery Process

The protocol was reverse-engineered iteratively through the `extract_init` script series:

### Phase 1: Brute Force (extract_init11.py)

Tested hypothesis: `total_packet_size = payload_length_xor + X` for various X values.

- Tried X = 2 through 30
- Found that **X = 6** (header size) produced the most valid packet chains
- Validated by checking if decoded sequences parse the entire stream

### Phase 2: Signature Scanning (extract_init12.py)

Refined header detection by looking for patterns:
- Byte[1] == 0x13 occurs in many headers (because `length XOR 0x1357` often has 0x13 in high byte)
- Only considered "plausible" payload lengths (0 < length < 10,000)
- Compared decrypted opcodes against known valid ranges

### Phase 3: Multiple Hypotheses (extract_init13.py)

Tested multiple header size assumptions simultaneously:
- X = 20 (initial guess from login server observation)
- X = 10
- X = 6 (eventually confirmed)
- Reported which formula parses most packets before first error

### Phase 4: Stable Parser (extract_init14.py)

Final implementation with proper handling:
- 6-byte header with XOR deobfuscation
- Compression detection via flags bit 0x80
- 16-byte XOR key extraction from Hello packet (bytes 12-27)
- Full stream parsing with position tracking and error recovery

**Key constant discovered**: `0x1357` — XOR mask for payload length in header bytes 0-1.

## Client Binary Analysis

### Ghidra Findings

| Metric | Count |
|--------|-------|
| Total functions analyzed | 7,090 |
| Dispatch tables identified | 338 |
| VTable classes found | 659 |
| Total opcodes mapped | 3,172 |

### Main Dispatch Table

`FUN_0048ae30` — 331 entries, the primary message handler. Routes incoming opcodes to handler functions.

Other notable dispatch tables:
- `FUN_0059e9f0` — 29 entries, high opcode range (0xC8C+)
- `FUN_005ed330` — Voice/chat commands
- `FUN_00581620` — 15 entries, opcodes 0x16A5-0x16B7

### Struct Analysis

The main game struct (`UnknownStruct_1`) is estimated at **195,656 bytes** with 267+ field positions. Key characteristics:
- Position fields heavily used (pos_1 through pos_267+)
- Pointer fields for dynamic data
- Accessed by 2,211 functions

### Strings of Interest

Found in client binary:
- Voice commands: `vc_gakkari`, `vc_ooiyo`, `vc_osoiyo` (Japanese voice lines)
- SQLite embedded: `sqlite3_extension_init` (client has local SQLite)
- Network: Connection state strings, error messages

## Known vs. Unknown

### Well Understood

| System | Coverage | Notes |
|--------|----------|-------|
| Packet header format | Complete | 6-byte XOR-obfuscated header |
| Encryption (CryptXOR) | Complete | Static 16-byte XOR key S->C |
| Encryption (CryptXORIV) | Complete | Evolving key C->S, DWORD addition |
| Login handshake | Complete | Hello -> Login -> Response -> PIN -> Redirect |
| World init sequence | Complete | Auth -> ACK -> Init packets -> Skill -> Area |
| Movement | Complete | Request/response with clamping |
| NPC spawning | Complete | 0x0008 format with sprite/type IDs |
| Dialog system | Mostly | Tree traversal works, dialog display opcode unknown |
| Keepalive | Complete | 0x018A tick (1s) and timer (60s) |
| Chat | Mostly | Send/receive works, channel types partially mapped |

### Partially Understood

| System | Status | What's Missing |
|--------|--------|----------------|
| Character stats (0x0042) | Field positions identified | Full field mapping of all 107 bytes |
| Combat | Basic framework | Damage formulas, skill effects, status effects |
| Inventory (0x0155) | Opcode identified | Slot format, item data structure |
| Equipment (0x0049) | Opcode identified | Equipment system, stat modifiers |
| Dialog display | Uses chat workaround | Real dialog opcode (likely 0x002B) unconfirmed |
| NPC shop | Opens with message | Shop packet format unknown |
| Quest system | Framework built | Quest trigger/completion opcodes unknown |
| Buff system (0x006A) | Structure identified | Buff IDs, duration handling |

### Not Yet Implemented

| System | Opcodes | Notes |
|--------|---------|-------|
| Pet system | 0x0101 | Pet commands, pet spawning |
| Voice commands | 0x1BF8-0x1CA3 | Japanese voice line triggers |
| Extended NPC data | 0x0041 | 114-161 byte NPC packets |
| Zone data | 0x00ED | 239-byte zone information |
| Skill slot management | 0x0122 | Hotbar assignment |
| Party system | - | Party creation, invites, tracking |
| Trading | - | Player-to-player trade |
| Mail system | - | In-game mail |
| Guild system | - | Guild creation, management |
| PvP | - | Player vs player combat |
| Crafting | - | Item creation system |
| Auction house | - | Item marketplace |
| File server protocol | Port 21238 | Asset serving (currently stub) |

## Research Files Location

### Analysis Scripts
```
Backup/Private Server Development/Angels Online/
    analyze_packets.py          # Basic packet structure analysis
    decode_0002.py              # Character definition field mapping
    decode_0042.py              # Character stats field mapping
    extract_payloads.py         # Multi-opcode payload extraction
    Alignment Test/
        extract_init11-14.py    # Header format discovery progression
```

### Binary Analysis Output
```
Backup/Private Server Development/Angels Online/Angel-analysis/
    dispatch/
        protocol_map.txt        # 338 dispatch tables, opcode->handler
        dispatch_tables.json    # Structured dispatch data (510KB)
    structs/
        structs.h               # Auto-generated C struct definitions
        structs.json            # Structured definitions (2.1MB)
    vtables/
        vtables.json            # VTable analysis (16MB)
        class_hierarchy.txt     # Class inheritance (1.5MB)
    serialization/
        serialization_functions.json  # Type mappings (3MB)
        primitive_groups.json         # Type groupings (168KB)
        serialization_map.txt         # Human-readable index (806KB)
    strings/
        strings_xref.json       # Cross-reference database (6.4MB)
        interesting_strings.txt # Filtered meaningful strings
    callgraph/
        callgraph.json          # Function call chains (2.5MB)
    split/
        SUMMARY.txt             # Function categorization
        index.json              # Function index (9.2MB)
```

### Gameplay Data
```
Backup/Private Server Development/Angels Online/
    Gameplay.pcap               # Full game session capture
    Gameplay_analysis.json      # Parsed opcode summaries
    Gameplay_decoded.json       # Decoded packet data (67K lines)
    CleanInvLevelUp_analysis.json  # Level-up session analysis
```

## Tips for Further Reverse Engineering

1. **New opcodes**: Use `Gameplay_decoded.json` to find opcodes not yet handled. Cross-reference with `dispatch_tables.json` to find the client handler function, then analyze in Ghidra.

2. **Field mapping**: Capture two sessions with known differences (e.g., different levels, different items) and diff the 0x0042 or 0x0155 packets to identify field positions.

3. **Dialog system**: The real dialog opcode is likely 0x002B based on code patterns. Capture a session with NPC dialog interaction and look for S->C packets immediately after C->S 0x0044.

4. **Inventory**: Opcode 0x0155 appears in init with 602+ bytes. Compare captures with different inventories to map slot positions.

5. **Combat formulas**: Monster stats are fully documented in monster.xml. The damage formula likely uses 平均攻擊 (avg attack), 防禦 (defense), and element modifiers. Check client functions near the 0x0019 dispatch handler.

6. **Serialization system**: The `serialization_functions.json` (3MB) maps function addresses to data types. This could reveal the complete struct layout for game objects.

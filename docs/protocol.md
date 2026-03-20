# Angels Online Protocol Documentation

This document describes the Angels Online network protocol as implemented by this private server.
All multi-byte integers are **little-endian**. Notation: `LE16` = 2-byte LE, `LE32` = 4-byte LE, `B` = 1 byte.

---

## Table of Contents

1. [Packet Framing](#1-packet-framing)
2. [Encryption](#2-encryption)
3. [Sub-Message Format](#3-sub-message-format)
4. [Login Flow](#4-login-flow)
5. [World Server — Client-to-Server Opcodes](#5-world-server--client-to-server-opcodes)
6. [World Server — Server-to-Client Opcodes](#6-world-server--server-to-client-opcodes)
7. [Initialization Sequence](#7-initialization-sequence)
8. [NPC Behaviors & Dialog System](#8-npc-behaviors--dialog-system)
9. [File Server](#9-file-server)
10. [Opcode Summary Table](#10-opcode-summary-table)

---

## 1. Packet Framing

Every TCP packet uses a **6-byte obfuscated header** followed by the payload.

### Header Structure (6 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | `payload_length XOR 0x1357` | Obfuscated payload length |
| 2 | LE16 | `sequence XOR payload_length` | Obfuscated sequence number |
| 4 | B | `flags XOR (payload_length & 0xFF)` | Obfuscated flags |
| 5 | B | `checksum` | Checksum of plaintext payload |

### Flags

| Bit | Mask | Meaning |
|-----|------|---------|
| 0 | 0x01 | Payload is encrypted |
| 7 | 0x80 | Payload is LZO-compressed |

### Padding

- If encrypted: payload is padded to the next 16-byte boundary
- If unencrypted: no padding

### Sequence Numbers

- Start at 1, increment per packet, range 1–0x7FFE
- Wrap from 0x7FFF back to 1
- Special value `0xFFFF` is used for the Hello/key-exchange packet

### Checksum Algorithm

```
1. val = 0xD31F
2. XOR all LE16 words of the plaintext payload (skip last byte if odd length)
3. Rotate the 16-bit result left by (result & 0xF) bits
4. Return (low_byte XOR high_byte)
```

---

## 2. Encryption

Two XOR ciphers are used, both operating with a **16-byte repeating key**.

### Static XOR (Server-to-Client)

- The 16-byte key is sent in the Hello packet and never changes
- `ciphertext[i] = plaintext[i] XOR key[i % 16]`

### Evolving XOR (Client-to-Server)

- Same 16-byte key from the Hello packet, but mutates after each packet
- After encrypting/decrypting, each 4-byte DWORD of the key is updated:
  ```
  for each dword in key[0..3]:
      dword = (dword + padded_payload_length) & 0xFFFFFFFF
  ```
- This makes the cipher stateful — packets must be processed in order

---

## 3. Sub-Message Format

Payloads contain one or more **sub-messages** concatenated together:

```
[LE16 sub_message_length][sub_message_data of that length]
[LE16 sub_message_length][sub_message_data of that length]
...
```

Each sub-message begins with a **LE16 opcode** identifying its type.

---

## 4. Login Flow

The login server runs on port **16768** by default.

### Phase 1: Hello (S->C)

Sent immediately on connection with sequence `0xFFFF`.

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0014 | Sub-message length (20 bytes) |
| 2 | LE16 | 0x0010 | Key length (16 bytes) |
| 4 | LE16 | 0x0000 | Reserved |
| 6 | 16B | random | XOR encryption key for this session |

Total payload: 22 bytes. Total packet: 28 bytes (6 header + 22 payload).

### Phase 2: Login Request (C->S)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | — | Sub-message length |
| 2 | LE16 | 0x0002 | Sub-type (login) |
| 4 | 8B | — | Username (null-padded ASCII) |
| 12+ | — | — | Password hash (structure TBD) |

### Phase 3: Login Response (S->C)

LZO-compressed payload containing two sub-messages:

#### Sub-message 1: MOTD (opcode 0x000C)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x000C | Opcode |
| 2 | LE32 | 0x69402978 | Flags |
| 6 | LE32 | — | Text length |
| 10 | var | — | MOTD text + null terminator |

#### Sub-message 2: Account Data (opcode 0x0000, 654 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0000 | Opcode |
| 4 | LE16 | 1 | Status (1 = success) |
| 6 | LE32 | 3 | Field |
| 16 | LE32 | — | Account ID |
| 36 | LE32 | — | Entity reference |
| 40 | 9B | — | Display name (null-padded) |
| 49 | LE32 | — | Character ID |
| 53 | LE32 | 154 | Field |
| 57 | 4B | — | Session key |
| 61 | 10B | — | Login name (null-padded) |
| 86 | LE32 | — | Level |
| 90 | LE32 | — | Class ID |
| 114 | LE32 | — | HP max |
| 118 | LE32 | — | MP max |
| 122+ | — | — | Remaining ~525 bytes (partially decoded) |

### Phase 4: PIN/Session (C->S)

Optional client response; some client versions send a PIN confirmation packet.

### Phase 5: Redirect (S->C, opcode 0x0004)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 32 | Sub-message length |
| 2 | LE16 | 0x0004 | Sub-type |
| 4 | LE16 | 0x0000 | Padding |
| 6 | 4B | random | Session token (used to look up player on world server) |
| 10 | B | 0x01 | Flag |
| 11 | 16B | — | IP address string (null-terminated, zero-padded) |
| 27 | LE16 | — | World server port |
| 29 | 5B | 0x00 | Padding |

---

## 5. World Server — Client-to-Server Opcodes

The world server runs on port **27901** by default.

### Handled Opcodes

| Opcode | Name | Min Size | Description |
|--------|------|----------|-------------|
| 0x0003 | ACK | — | Connection acknowledgment, triggers init data |
| 0x0004 | MOVEMENT | 15B | Movement request. Bytes 11-12: dest_x, 13-14: dest_y |
| 0x0005 | ENTITY_ACTION_ALT | 8B | Alternate entity action (routed to NPC handler) |
| 0x0006 | ENTITY_SELECT | 8B | Select/click entity. Bytes 4-7: target_entity_id |
| 0x0009 | STOP_ACTION | — | Cancel current action |
| 0x000D | ENTITY_ACTION | 8B | Interact with NPC/entity. Bytes 4-7: runtime_entity_id |
| 0x000F | TARGET_MOB | 8B | Target a monster. Bytes 4-7: mob_id |
| 0x0012 | BUY_SELL | 8B | Shop transaction. Bytes 4-5: item_id, 6-7: quantity |
| 0x0016 | USE_SKILL | 9B | Use a skill. Byte 4: skill_id, bytes 5-8: target_id |
| 0x001A | REQ_PLAYER_DETAILS | 8B | Request player info |
| 0x002E | CHAT_SEND | 5B | Send chat. Byte 4: channel, byte 5+: text |
| 0x003E | TOGGLE_ACTION | 5B | Sit/stand/meditate. Byte 4: action_id |
| 0x0044 | NPC_DIALOG | 9B | Select dialog option |
| 0x0143 | ZONE_READY | — | Client finished loading zone |
| 0x0150 | EMOTE | 6B | Send emote. Bytes 4-5: emote_id |

### Silent Opcodes (acknowledged, no response)

| Opcode | Name |
|--------|------|
| 0x0007 | ENTITY_POS_ACK |
| 0x000B | ENTITY_STATUS_ACK |
| 0x000E | ENTITY_SPAWN_ACK |
| 0x015E | PING |

### Known but Unimplemented Opcodes

| Opcode | Name (if known) |
|--------|-----------------|
| 0x0011 | Unknown |
| 0x0017 | Unknown |
| 0x0018 | Unknown |
| 0x0027 | Unknown |
| 0x002C | INSPECT_PLAYER |
| 0x0034 | CANCEL_ACTION |
| 0x0048 | Unknown |
| 0x0049 | Unknown |
| 0x0101 | Unknown |
| 0x0122 | Unknown |
| 0x0127 | Unknown |
| 0x0128 | CHAT_VARIANT |
| 0x012B | Unknown |
| 0x012D | Unknown |
| 0x0133 | Unknown |
| 0x0139 | Unknown |
| 0x0152 | ANTI_AFK_TICK |

---

## 6. World Server — Server-to-Client Opcodes

### Entity Spawning

#### 0x0008 — NPC/Monster Spawn (65 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0008 | Opcode |
| 2 | LE16 | — | Entity ID (low) |
| 4 | LE16 | — | Entity ID (high) |
| 6 | LE32 | — | Unknown |
| 10 | LE32 | — | Position X |
| 14 | LE32 | — | Position Y |
| 18 | 13B | — | Name (null-padded) |
| 31 | 34B | — | Extra data (see below) |

Extra data varies by entity type:
- **NPC**: `extra[4]=0x05`, `extra[5:7]=sprite_id`, `extra[11]=0xC8`, `extra[16:18]=npc_type_id`
- **Monster**: `extra[3]=0x01`, `extra[5:7]=sprite_id`, `extra[11]=0x01`, `extra[16:18]=monster_type_id`

#### 0x000E — Entity Spawn (45 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x000E | Opcode |
| 2 | LE16 | — | Entity ID (low) |
| 4 | LE16 | — | Entity ID (high) |
| 6 | LE32 | — | Unknown |
| 10 | LE32 | — | Position X |
| 14 | LE32 | — | Position Y |
| 18 | 17B | — | Name bytes |
| 35 | 8B | — | Tail bytes |

#### 0x000F — Mob Spawn (46 bytes)

Same layout as 0x000E but 46 bytes with 18-byte name field.

#### 0x001B — Entity Despawn (14 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x001B | Opcode |
| 2 | LE32 | — | Entity ID |
| 6 | LE16 | 0x0102 | Flags |
| 8 | LE32 | — | Area ID |
| 12 | LE16 | 0x0000 | Padding |

### Movement & Position

#### 0x0005 — Entity Position/Animation (24 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0005 | Opcode |
| 2 | LE16 | — | Entity ID (low) |
| 4 | LE16 | — | Entity ID (high) |
| 6 | LE32 | — | Current X |
| 10 | LE32 | — | Current Y |
| 14 | LE32 | — | Destination X |
| 18 | LE32 | — | Destination Y |
| 22 | LE16 | — | Speed |

#### 0x0007 — Entity Position Marker (7 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0007 | Opcode |
| 2 | LE16 | — | Entity ID (low) |
| 4 | LE16 | — | Entity ID (high) |
| 6 | B | — | Flag |

#### 0x0018 — Entity Move (26 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0018 | Opcode |
| 2 | LE16 | 0x0005 | Sub-type |
| 4 | LE32 | — | Entity ID |
| 8 | LE32 | — | Current X |
| 12 | LE32 | — | Current Y |
| 16 | LE32 | — | Destination X |
| 20 | LE32 | — | Destination Y |
| 24 | LE16 | — | Speed |

#### 0x006D — Movement Response Wrapper

Wraps a 0x0005 entity position sub-message. Total payload:

```
[LE16 2][LE16 0x006D][LE16 24][LE16 0x0005][entity_id...speed]
```

### Character & Stats

#### 0x0042 — Character Stats (107 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0042 | Opcode |
| 2 | LE32 | — | HP |
| 6 | LE32 | — | HP max |
| 10 | LE32 | — | MP |
| 14 | LE32 | — | MP max |
| 18 | 89B | — | Stats tail (partially decoded) |

#### 0x0001 — Player Spawn (134–182 bytes, variable)

Complex structure containing entity ID, level, class, name, guild tag, skills, buffs. Layout partially decoded; current implementation patches known fields into a captured template.

#### 0x0028 — Player Appears (85 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0028 | Opcode |
| 2 | LE32 | — | Area ID |
| 6 | B | 1 | Spawn flag |
| 7 | 10B | — | Player name |
| 17 | 4B | — | Unknown |
| 21 | 4B | "AMe\0" | Tag |
| 25 | LE32 | — | Entity ID |
| 29+ | — | — | Appearance, class data, buffs, guild |

### Combat

#### 0x000B — Entity Status (13 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x000B | Opcode |
| 2 | LE32 | — | Entity ID |
| 6 | B | — | Status A (0x01=alive, 0x07=damaged) |
| 7 | B | — | Status B |
| 8 | 5B | 0x00 | Padding |

#### 0x0019 — Combat Action (27+ bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0019 | Opcode |
| 2 | LE32 | — | Source entity ID |
| 6 | LE32 | — | Target entity ID |
| 10 | LE16 | — | Action type |
| 12 | LE16 | — | Skill ID |
| 14 | LE32 | — | Damage |
| 18 | LE16 | — | Flags |
| 20 | 5B | 0x00 | Padding |

### Chat & Social

#### 0x001E — Chat Message (variable)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x001E | Opcode |
| 2 | LE16 | 0x0001 | Chat type |
| 4 | LE32 | — | Entity ID |
| 8 | 8B | — | Name (null-padded) |
| 16 | LE32 | — | Position X |
| 20 | LE32 | — | Position Y |
| 24 | B | — | Channel |
| 25 | var | — | Message (null-terminated) |

#### 0x0128 — World Chat (variable)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0128 | Opcode |
| 2 | LE32 | — | Source entity or channel |
| 6 | B | — | Channel (0x0A=world, 0x0B=shout, 0x02=party) |
| 7 | 16B | — | Sender name field |
| 23+ | var | — | Message (null-terminated) |

#### 0x003A — Inspect Player Response (19 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x003A | Opcode |
| 2 | 10B | — | Name (null-padded) |
| 12 | LE16 | — | Level |
| 14 | LE32 | — | Entity ID |
| 18 | B | 1 | Flag |

#### 0x001F — Player Title (variable)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x001F | Opcode |
| 2 | LE32 | — | Entity ID |
| 6 | 2B | "PT" | Prefix |
| 8 | 25B | — | Title (null-terminated) |

### Entity Properties

#### 0x001D — Entity Setting (16 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x001D | Opcode |
| 2 | LE16 | — | Entity ID (low) |
| 4 | LE16 | — | Entity ID (high) |
| 6 | LE16 | — | Marker |
| 8 | LE16 | — | Setting ID |
| 10 | LE16 | — | Value (low) |
| 12 | LE16 | — | Value |
| 14 | LE16 | — | Value (high) |

#### 0x000D — Entity Action (variable, ~15+ bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x000D | Opcode |
| 2 | LE32 | — | Entity ID |
| 6 | LE16 | — | Action type |
| 8 | LE32 | — | Target ID |
| 12 | LE16 | — | Data |

### Hotbar & Inventory

#### 0x0158 — Skill/Hotbar Slot (74 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0158 | Opcode |
| 2 | 72B | — | Slot data |

34 instances sent during initialization.

#### 0x005B — Slot Table (218 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x005B | Opcode |
| 2 | 216B | — | 24 slots x 9 bytes each: `[B active][B index][7B zeros]` |

#### 0x0063 — Equip Result (8 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0063 | Opcode |
| 2 | LE32 | 0 | Result (0 = success) |
| 6 | LE16 | — | Slot |

### Currency & Economy

#### 0x0149 — Currency/Gold (38 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0149 | Opcode |
| 2 | LE32 | 1 | Type |
| 6 | LE32 | 0 | Unknown |
| 10 | LE32 | — | Amount |
| 14 | 20B | 0x00 | Padding |
| 34 | B | 5 | Flag |
| 35 | 3B | — | Tail |

### Zone & Map

#### 0x018E — Zone List (166 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x018E | Opcode |
| 2 | LE32 | 1 | Unknown |
| 6 | 160B | — | 20 slots x 8 bytes: `[LE32 zone_id][LE32 zero]` |

#### 0x0021 — Entity Anchor (24 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0021 | Opcode |
| 2 | LE32 | — | Index |
| 6 | LE32 | — | Area ID |
| 10 | LE32 | — | Map ID |
| 14 | 6B | 0x00 | Padding |
| 20 | 4B | `00 C3 A0 01` | Constant |

#### 0x0027, 0x003F, 0x0040 — Area Reference (10 bytes each)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | opcode | 0x0027, 0x003F, or 0x0040 |
| 2 | LE32 | — | Area ID |
| 6 | LE32 | 0 | Padding |

### Keepalive & Timers

#### 0x018A — Keepalive Tick (10 bytes, sent every ~1s)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x018A | Opcode |
| 2 | LE32 | 4 | Type |
| 6 | LE32 | 0 | Data |

#### 0x018A — Timer (14 bytes, sent every ~60s)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x018A | Opcode |
| 2 | LE32 | 8 | Type |
| 6 | LE32 | — | Minutes elapsed |
| 10 | LE32 | 0 | Padding |

#### 0x0015 — Server Timer Broadcast (7 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0015 | Opcode |
| 2 | B | 1 | Flag |
| 3 | LE32 | — | Minutes |

### Buffs & Status

#### 0x006A — Buff Info (variable, 108–134 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x006A | Opcode |
| 2 | LE32 | — | Entity ID |
| 6 | LE16 | — | Buff count |
| 8 | var | — | Per buff: `[LE16 buff_id][LE32 duration_ms][LE32 caster_id]` |

#### 0x0013 — Pet Status Tick (10 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0013 | Opcode |
| 2 | LE32 | — | Pet entity ID |
| 6 | LE16 | — | Status |
| 8 | LE16 | — | HP |

### Miscellaneous

#### 0x0022 — Loot Announce (24 bytes)

| Offset | Size | Value | Description |
|--------|------|-------|-------------|
| 0 | LE16 | 0x0022 | Opcode |
| 2 | LE32 | — | Entity ID |
| 6 | LE32 | — | Area ID |
| 10 | LE32 | — | Loot ID |
| 14 | 10B | 0x00 | Padding |

### Initialization Constants

These are fixed-value packets sent during the init sequence:

| Opcode | Size | Description |
|--------|------|-------------|
| 0x005C | 39B | opcode + 37 zero bytes |
| 0x005D | 6B | opcode + LE32 timestamp |
| 0x0142 | 3B | opcode + B flag |
| 0x0144 | 8B | opcode + 6 zero bytes |
| 0x0145 | 4B | opcode + 2 zero bytes |
| 0x0162 | 10B | opcode + 8 zero bytes |
| 0x0164 | 19B | opcode + 17 zero bytes |
| 0x017D | 4B | opcode + 2 zero bytes |
| 0x0185 | 14B | opcode + LE32 entity_id + 8 zero bytes |
| 0x012A | 23B | opcode + LE32 index + 17 zero bytes |
| 0x014A | 19B | Party/event name: opcode + 12B name + 5B tail |
| 0x0191 | 10B | Session config: opcode + LE32 a + LE32 b (default 0, 50) |
| 0x0160 | 27B | Settings: opcode + LE32 flag + 9B zeros + 3x LE32 values |
| 0x016F | 20B | Flags: opcode + LE32 type + B flag_a + B flag_b + 3B zeros + B flag_c + 8B zeros |
| 0x017A | 19B | Data: opcode + LE32 a + LE32 b + 9B zeros |
| 0x018F | 14B | Three values: opcode + 3x LE32 |
| 0x0014 | 9B | Flag/mode: opcode + LE32 value + B mode + B type_a + B type_b |

---

## 7. Initialization Sequence

When a client connects to the world server and sends ACK (0x0003), the server responds with a multi-packet init sequence:

### Init Packet 1 (~4120 bytes, compressed)

Contains the character definition. Key dynamic fields patched from database:

| Byte Offset | Field |
|-------------|-------|
| 4–7 | Entity ID |
| 20–23 | HP |
| 24–27 | HP max |
| 28–31 | MP |
| 32–35 | MP max |
| ~3830–3833 | Map ID |
| ~4100–4103 | Unix timestamp |

The rest is a captured template preserving appearance, equipment, skills, and buffs.

### Init Packet 2 (~1500 bytes, compressed)

Entity spawn data and world settings. Entity ID is patched at offset 4.

### Init Packet 3 (~120 bytes, uncompressed)

Contains: 0x0042 (stats), 0x005D (timestamp), multiple 0x0158 (hotbar slots).

### Init Packet 4 (variable, uncompressed)

Contains fixed structures: 0x005C, 0x005B (slot table), 0x012A, 0x014A, 0x0185, 0x0162, 0x0164, 0x0144, 0x0145, 0x017D, 0x0142, 0x0149 (gold), 0x0160, 0x016F, 0x017A, 0x018A, 0x018E (zone list), 0x018F, 0x0191.

### Area Packets (17 packets, uncompressed)

After init, 17 area entity packets are sent containing NPC spawns (0x0008), entity spawns (0x000E), mob spawns (0x000F), entity positions (0x0005), and area references (0x0027, 0x003F, 0x0040).

---

## 8. NPC Behaviors & Dialog System

### Hardcoded NPC Behaviors

| NPC Type | Type | Purpose |
|----------|------|---------|
| 2006 | quest_npc | Census Angel — class selection |
| 2429 | shop | Merchant NPC |
| 8804 | totem | Aurora Totem teleporter |
| 1553 | gate | House Pickets gate (to map 3) |
| 1554 | gate | Gaoler Angel gate (to map 3) |
| 1938 | totem | Dark City Totem |
| 1940 | totem | Breeze Totem |

### NPC Interaction Flow

1. Client sends **0x000D** with runtime entity ID
2. Server looks up `npc_type_id` in the entity registry
3. Checks `NPC_BEHAVIORS` dict for hardcoded behavior
4. Falls back to dialog system if no hardcoded entry
5. Sends response (chat message, zone transfer, shop open, etc.)

### Dialog System

- Dialog trees loaded from `game_xml/` (703 global + per-map local dialogs)
- State tracked in `session['dialog_state']`
- Client selects options via **0x0044** (NPC_DIALOG)
- Dialog actions support zone warps (action type 37)
- Currently rendered as chat messages (0x001E) since the native dialog packet (0x002B) layout is unconfirmed

---

## 9. File Server

- Port: **21238** (default)
- Status: Stub implementation
- The client connects to the file server for asset verification but the protocol is not yet captured

---

## 10. Opcode Summary Table

### Client-to-Server

| Opcode | Name | Status |
|--------|------|--------|
| 0x0003 | ACK | Handled |
| 0x0004 | MOVEMENT | Handled |
| 0x0005 | ENTITY_ACTION_ALT | Handled |
| 0x0006 | ENTITY_SELECT | Handled |
| 0x0007 | ENTITY_POS_ACK | Silent |
| 0x0009 | STOP_ACTION | Handled |
| 0x000B | ENTITY_STATUS_ACK | Silent |
| 0x000D | ENTITY_ACTION | Handled |
| 0x000E | ENTITY_SPAWN_ACK | Silent |
| 0x000F | TARGET_MOB | Handled |
| 0x0011 | Unknown | Logged |
| 0x0012 | BUY_SELL | Handled |
| 0x0016 | USE_SKILL | Handled |
| 0x0017 | Unknown | Logged |
| 0x0018 | Unknown | Logged |
| 0x001A | REQ_PLAYER_DETAILS | Handled |
| 0x0027 | Unknown | Logged |
| 0x002C | INSPECT_PLAYER | Logged |
| 0x002E | CHAT_SEND | Handled |
| 0x0034 | CANCEL_ACTION | Logged |
| 0x003E | TOGGLE_ACTION | Handled |
| 0x0044 | NPC_DIALOG | Handled |
| 0x0048 | Unknown | Logged |
| 0x0049 | Unknown | Logged |
| 0x0101 | Unknown | Logged |
| 0x0122 | Unknown | Logged |
| 0x0127 | Unknown | Logged |
| 0x0128 | CHAT_VARIANT | Logged |
| 0x012B | Unknown | Logged |
| 0x012D | Unknown | Logged |
| 0x0133 | Unknown | Logged |
| 0x0139 | Unknown | Logged |
| 0x0143 | ZONE_READY | Handled |
| 0x0150 | EMOTE | Handled |
| 0x0152 | ANTI_AFK_TICK | Silent |
| 0x015E | PING | Silent |

### Server-to-Client

| Opcode | Name | Size |
|--------|------|------|
| 0x0001 | PLAYER_SPAWN | 134–182B |
| 0x0005 | ENTITY_POSITION | 24B |
| 0x0007 | ENTITY_POS_MARKER | 7B |
| 0x0008 | NPC_MONSTER_SPAWN | 65B |
| 0x000B | ENTITY_STATUS | 13B |
| 0x000D | ENTITY_ACTION | ~15B+ |
| 0x000E | ENTITY_SPAWN | 45B |
| 0x000F | MOB_SPAWN | 46B |
| 0x0013 | PET_STATUS | 10B |
| 0x0014 | FLAG_MODE | 9B |
| 0x0015 | SERVER_TIMER | 7B |
| 0x0018 | ENTITY_MOVE | 26B |
| 0x0019 | COMBAT_ACTION | 27B+ |
| 0x001B | ENTITY_DESPAWN | 14B |
| 0x001D | ENTITY_SETTING | 16B |
| 0x001E | CHAT_MESSAGE | var |
| 0x001F | PLAYER_TITLE | var |
| 0x0021 | ENTITY_ANCHOR | 24B |
| 0x0022 | LOOT_ANNOUNCE | 24B |
| 0x0027 | AREA_REF | 10B |
| 0x0028 | PLAYER_APPEARS | 85B |
| 0x003A | INSPECT_RESPONSE | 19B |
| 0x003F | AREA_REF | 10B |
| 0x0040 | AREA_REF | 10B |
| 0x0042 | CHARACTER_STATS | 107B |
| 0x005B | SLOT_TABLE | 218B |
| 0x005C | INIT_CONST | 39B |
| 0x005D | TIMESTAMP | 6B |
| 0x0063 | EQUIP_RESULT | 8B |
| 0x006A | BUFF_INFO | 108–134B |
| 0x006D | MOVEMENT_RESP | wrapper |
| 0x0128 | WORLD_CHAT | var |
| 0x012A | INDEXED_SLOT | 23B |
| 0x0142 | TOGGLE_FLAG | 3B |
| 0x0144 | INIT_CONST | 8B |
| 0x0145 | INIT_CONST | 4B |
| 0x0149 | CURRENCY | 38B |
| 0x014A | PARTY_NAME | 19B |
| 0x0158 | HOTBAR_SLOT | 74B |
| 0x0160 | SETTINGS | 27B |
| 0x0162 | INIT_CONST | 10B |
| 0x0164 | INIT_CONST | 19B |
| 0x016F | FLAGS | 20B |
| 0x017A | DATA | 19B |
| 0x017D | INIT_CONST | 4B |
| 0x0185 | ENTITY_REF | 14B |
| 0x018A | KEEPALIVE | 10B/14B |
| 0x018E | ZONE_LIST | 166B |
| 0x018F | THREE_VALUES | 14B |
| 0x0191 | SESSION_CONFIG | 10B |

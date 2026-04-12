# Opcode Reference

All known opcodes with packet layouts and field byte offsets. Sizes listed are for the sub-message data (after the LE16 length prefix).

## S->C Opcodes (Server to Client)

### 0x0001 — Player Spawn (134-182 bytes)
Sent when a player appears in view. Contains entity_id, level, class, name, guild, appearance.

### 0x0005 — Position Update (24 bytes)
```
Offset  Size  Field
0       2B    opcode = 0x0005
2       2B    entity_id_lo
4       2B    entity_id_hi
6       4B    x1 (LE32, current X)
10      4B    y1 (LE32, current Y)
14      4B    x2 (LE32, destination X)
18      4B    y2 (LE32, destination Y)
22      2B    speed (LE16, movement speed)
```

### 0x0007 — Entity Position Marker (7 bytes)
```
0       2B    opcode = 0x0007
2       2B    entity_id_lo
4       2B    entity_id_hi
6       1B    flag
```

### 0x0008 — NPC Spawn (65 bytes)
```
0       2B    opcode = 0x0008
2       2B    entity_id_lo
4       2B    entity_id_hi
6       4B    unk1 (LE32, 1=NPC 0=monster)
10      4B    tile_x (LE32)
14      4B    tile_y (LE32)
18      13B   name (null-padded ASCII)
31      34B   extra data:
  NPC:
    +4  = 0x05 (marker)
    +5  = LE16 sprite_id
    +11 = 0xC8 (marker)
    +16 = LE16 npc_type_id
  Monster:
    +3  = 0x01
    +5  = LE16 sprite_id
    +9  = LE16(7)
    +11 = 0x01
    +15 = 0x04
    +16 = LE16 monster_type_id
    +18 = LE16(0x8008)
```

### 0x000B — Entity Status (13 bytes)
```
0       2B    opcode = 0x000B
2       4B    entity_id (LE32)
6       1B    status_a (1=alive, 7=damaged, 0=dead)
7       1B    status_b (secondary)
8       5B    zeros
```

### 0x000C — MOTD (variable)
```
0       2B    opcode = 0x000C
2       4B    flags = 0x69402978
6       4B    text_length (LE32)
10      var   text (ASCII + null)
```

### 0x000D — Entity Action (15+ bytes, variable)
```
0       2B    opcode = 0x000D
2       4B    entity_id (LE32)
6       2B    action_type (LE16)
8       4B    target_id (LE32)
12      2B    data (LE16)
```

### 0x000E — Entity Spawn (45 bytes)
```
0       2B    opcode = 0x000E
2       2B    entity_id_lo
4       2B    entity_id_hi
6       4B    unk1 (LE32)
10      4B    pos_x (LE32)
14      4B    pos_y (LE32)
18      var   name_bytes
var     var   tail_bytes
```

### 0x000F — Mob Spawn (46 bytes)
Same layout as 0x000E but with 1 extra byte.

### 0x0013 — Pet Status Tick (10 bytes)
```
0       2B    opcode = 0x0013
2       4B    pet_entity_id (LE32)
6       2B    status (LE16)
8       2B    hp (LE16)
```

### 0x0014 — Flag/Mode (9 bytes)
```
0       2B    opcode = 0x0014
2       4B    value (LE32)
6       1B    mode
7       1B    type_a
8       1B    type_b
```

### 0x0015 — Server Timer (7 bytes)
```
0       2B    opcode = 0x0015
2       1B    flag
3       4B    minutes (LE32)
```

### 0x0018 — Entity Move (26 bytes)
```
0       2B    opcode = 0x0018
2       2B    sub_type = 0x0005
4       4B    entity_id (LE32)
8       4B    cur_x (LE32)
12      4B    cur_y (LE32)
16      4B    dst_x (LE32)
20      4B    dst_y (LE32)
24      2B    speed (LE16)
```

### 0x0019 — Combat Action (27-53 bytes)
```
0       2B    opcode = 0x0019
2       4B    source_entity (LE32)
6       4B    target_entity (LE32)
10      2B    action_type (LE16, 1=basic 2=skill 3=miss)
12      2B    skill_id (LE16)
14      4B    damage (LE32)
18      2B    flags (LE16, crit=0x01 miss=0x02)
20      5B    zeros
```

### 0x001B — Entity Despawn (14 bytes)
```
0       2B    opcode = 0x001B
2       4B    entity_id (LE32)
6       2B    flags (LE16)
8       4B    area_id (LE32)
12      2B    zeros
```

### 0x001D — Entity Setting (16 bytes)
```
0       2B    opcode = 0x001D
2       2B    entity_id_lo
4       2B    entity_id_hi
6       2B    marker (LE16)
8       2B    setting_id (LE16)
10      2B    value_lo (LE16)
12      2B    value (LE16)
14      2B    value_hi (LE16)
```

### 0x001E — Chat Message (variable)
```
0       2B    opcode = 0x001E
2       2B    chat_type (LE16, 0x0001=system 0x0017=player)
4       4B    entity_id (LE32, sender)
8       8B    sender_name (null-padded)
16      4B    pos_x (LE32)
20      4B    pos_y (LE32)
24      1B    channel (0x01=system 0x00=normal)
25      var   message (null-terminated, max 200 chars)
```

### 0x001E — Area Entity Reference (6 bytes)
```
0       2B    opcode = 0x001E
2       4B    entity_ref (LE32)
```
Note: Same opcode as chat — context-dependent. This variant appears in init packets.

### 0x001F — Player Title (variable)
```
0       2B    opcode = 0x001F
2       4B    entity_id (LE32)
6       2B    prefix "PT"
8       var   name/title (null-terminated, max 24B)
```

### 0x0021 — Entity Anchor (24 bytes)
```
0       2B    opcode = 0x0021
2       4B    index (LE32)
6       4B    area_id (LE32, typically 0x0020A0C3)
10      4B    map_id (LE32)
14      6B    zeros
20      4B    trailing data
```

### 0x0022 — Loot Announce (24 bytes)
```
0       2B    opcode = 0x0022
2       4B    entity_id (LE32)
6       4B    area_id (LE32)
10      4B    loot_id (LE32)
14      10B   zeros
```

### 0x0027, 0x003F, 0x0040 — Area Reference (10 bytes each)
```
0       2B    opcode
2       4B    area_id (LE32, typically 0x0020A0C3)
6       4B    zeros
```

### 0x0028 — Player Appears (85 bytes)
Sent when another player enters view range. Fixed 85-byte format with name, entity_id, class, appearance, guild info.

### 0x003A — Inspect Player Response (19 bytes)
```
0       2B    opcode = 0x003A
2       10B   name (null-padded)
12      2B    level (LE16)
14      4B    entity_id (LE32)
18      1B    flag
```

### 0x0042 — Character Stats (107 bytes)
```
0       2B    opcode = 0x0042
2       4B    hp (LE32)
6       4B    hp_max (LE32)
10      4B    mp (LE32)
14      4B    mp_max (LE32)
18      89B   stats_tail (base stats, resistances, weight, etc.)
```

### 0x005B — Slot Table (218 bytes)
24 slots, each 9 bytes: `[1B active][1B index][7B zeros]`

### 0x005C — Empty (39 bytes)
`[opcode][37B zeros]`

### 0x005D — Timestamp (6 bytes)
```
0       2B    opcode = 0x005D
2       4B    unix_timestamp (LE32)
```

### 0x0063 — Equip Result (8 bytes)
```
0       2B    opcode = 0x0063
2       4B    result (LE32, 0=success)
6       2B    slot (LE16)
```

### 0x006A — Buff Info (108-134 bytes)
```
0       2B    opcode = 0x006A
2       4B    entity_id (LE32)
6       2B    buff_count (LE16)
8       N*10B buffs: [LE16 buff_id][LE32 duration_ms][LE32 caster_id]
```

### 0x006D — Movement Response Flag (2 bytes)
Used as first sub-message in movement response, followed by 0x0005 position.
```
0       2B    opcode = 0x006D
```

### 0x0128 — World Chat (variable)
```
0       2B    opcode = 0x0128
2       4B    source (LE32)
6       1B    channel (0x0a=world 0x0b=shout 0x02=party)
7       var   sender_name (null-terminated)
var     var   message (null-terminated)
```

### 0x012A — Indexed Slot (23 bytes)
```
0       2B    opcode = 0x012A
2       4B    index (LE32)
6       17B   zeros
```

### 0x0142 — Toggle Flag (3 bytes)
```
0       2B    opcode = 0x0142
2       1B    flag
```

### 0x0144 — Empty (8B), 0x0145 — Empty (4B), 0x0162 — Empty (10B), 0x0164 — Empty (19B), 0x017D — Empty (4B)
Zero-filled constant packets sent during init.

### 0x0149 — Currency (38 bytes)
```
0       2B    opcode = 0x0149
2       4B    currency_type (LE32)
6       4B    zeros
10      4B    amount (LE32)
14      20B   zeros
34      1B    flag
35      2B    tail (LE16)
37      1B    zero
```

### 0x014A — Party Name (19 bytes)
```
0       2B    opcode = 0x014A
2       12B   name (null-terminated)
14      5B    event flags
```

### 0x0158 — Skill Slot (74 bytes)
```
0       2B    opcode = 0x0158
2       72B   slot_data
```
34 instances sent during init (skill/hotbar data).

### 0x0160 — Settings (27 bytes)
```
0       2B    opcode = 0x0160
2       4B    flag (LE32)
6       9B    zeros
15      4B    val_a (LE32)
19      4B    val_b (LE32)
23      4B    val_c (LE32)
```

### 0x016F — Flags (20 bytes)
```
0       2B    opcode = 0x016F
2       4B    type_id (LE32)
6       1B    flag_a
7       1B    flag_b
8       3B    zeros
11      1B    flag_c
12      8B    zeros
```

### 0x0178 — Two-Value (10 bytes)
`[opcode][LE32 value][LE32 trailing]`

### 0x017A — Data (19 bytes)
`[opcode][LE32 a][LE32 b][9B zeros]`

### 0x0185 — Entity Reference (14 bytes)
```
0       2B    opcode = 0x0185
2       4B    entity_id (LE32)
6       8B    zeros
```

### 0x018A — Keepalive (10 or 14 bytes)
```
Tick (10B):
  [opcode][LE32 type=4][LE32 data=0]

Timer (14B):
  [opcode][LE32 type=8][LE32 minute_count][LE32 zeros]
```

### 0x018E — Zone List (166 bytes)
```
0       2B    opcode = 0x018E
2       4B    unk = 1 (LE32)
6       160B  20 x (LE32 zone_id, LE32 zero)
```

### 0x018F — Three-Value (14 bytes)
`[opcode][LE32 a][LE32 b][LE32 c]`

### 0x0191 — Session Config (10 bytes)
`[opcode][LE32 a][LE32 b]` (b=50, possibly tick interval)

---

## C->S Opcodes (Client to Server)

All C->S payloads start with: `[LE16 counter][LE16 opcode]` (4 bytes), then opcode-specific data.

| Opcode | Name | Min Size | Fields After Header |
|--------|------|----------|---------------------|
| 0x0003 | ACK | 4B | (none) |
| 0x0004 | MOVEMENT | 15B | `[7B unk][LE16 dest_x at +11][LE16 dest_y at +13]` |
| 0x0005 | ENTITY_POS | 8B | `[LE32 entity_id]` |
| 0x0006 | ENTITY_SELECT | 8B | `[LE32 target_entity_id at +4]` |
| 0x0007 | ENTITY_POS_ACK | 4B | (silent) |
| 0x0009 | STOP_ACTION | 4B | (clears dialog state) |
| 0x000B | ENTITY_STATUS_ACK | 4B | (silent) |
| 0x000D | ENTITY_ACTION | 8B | `[LE32 runtime_entity_id at +4]` |
| 0x000E | ENTITY_SPAWN_ACK | 4B | (silent) |
| 0x000F | TARGET_MOB | 8B | `[LE32 mob_id at +4]` |
| 0x0012 | BUY_SELL | 8B | `[LE16 item_id at +4][LE16 quantity at +6]` |
| 0x0016 | USE_SKILL | 9B | `[1B skill_id at +4][LE32 target_id at +5]` |
| 0x0019 | ENTITY_ACTION_2 | 8B | Same handler as 0x000D |
| 0x001A | PLAYER_DETAILS | 8B | `[LE32 target_entity_id at +4]` |
| 0x002C | INSPECT_PLAYER | 8B | `[LE32 target_entity_id at +4]` |
| 0x002E | CHAT_SEND | 5B | `[1B channel][var message (null-term)]` |
| 0x0034 | CANCEL_ACTION | 4B | (clears dialog state) |
| 0x003E | TOGGLE_ACTION | 5B | `[1B action_id at +4]` |
| 0x0044 | NPC_DIALOG | 9B | `[4B unk][1B option_index at +8]` |
| 0x0143 | ZONE_READY | 4B | (triggers keepalive tick response) |
| 0x0150 | EMOTE | 6B | `[LE16 emote_id at +4]` |
| 0x0152 | ANTI_AFK_TICK | 4B | (silent) |
| 0x015E | PING | 5B | (silent) |

---

## Opcode Categories

### Init Sequence (S->C)
0x0185, 0x0021, 0x005D, 0x0042, 0x0149, 0x001D, 0x0005, 0x0007, 0x0008, 0x000E, 0x000F, 0x005B, 0x005C, 0x0144, 0x0145, 0x0162, 0x0164, 0x017D, 0x0014, 0x001E, 0x0027, 0x003F, 0x0040, 0x012A, 0x0142, 0x0149, 0x014A, 0x0158, 0x0160, 0x016F, 0x0178, 0x017A, 0x018A, 0x018E, 0x018F, 0x0191

### Gameplay (bidirectional)
Movement: 0x0004 (C), 0x0005 (S), 0x006D+0x0005 (S response)
Combat: 0x000F (C), 0x0016 (C), 0x0019 (S), 0x000B (S)
NPC: 0x000D (C), 0x0044 (C), 0x001E (S chat), 0x002B (S dialog)
Social: 0x002E (C), 0x001E (S), 0x0128 (S world chat), 0x0150 (C)
Entity: 0x0008 (S spawn), 0x001B (S despawn), 0x0018 (S move), 0x0028 (S player appears)

### Keepalive
0x018A (S, every 1s tick / every 60s timer)
0x0003 (C->S ACK, S->C ACK response)

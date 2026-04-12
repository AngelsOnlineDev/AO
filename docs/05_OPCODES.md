# Opcode reference

Every opcode we've observed or implemented, grouped by direction and by role. Sizes are for the **sub-message body** (everything after the `[LE16 sub_len]` frame prefix, including the 2-byte opcode itself).

Status markers:
- ✅ confirmed against client decompile + empirical tests
- ❓ partially understood — fields work in practice but some bytes are unmapped or guessed
- ❌ opcode we see but have never decoded

If an opcode isn't listed here, either we've never seen it or we log-and-ignore it.

---

## S→C (server → client)

### Character & player

#### `0x0001` — Player spawn (140B) ✅

The rich player-spawn packet. Sent by our multiplayer presence broadcast. Client handler: `sub_5E97F0`.

```
00-01  LE16  opcode = 0x0001
02-05  LE32  entity_id
06-09  LE32  flags → model+480 (capture: 0x8D)
10-13  LE32  tile_x
14-17  LE32  tile_y
18-33  16B   character name, NUL-padded
35-49  15B   guild name, NUL-terminated
52     u8    direction (<<21 = facing bearing)
53-56  LE32  flags → model+572
57-61  5B    appearance bytes 1-5 → model+576..+580
62     u8    faction → model+744  (NOT class_id; see 09_RE)
63     u8    state → model+958 (7-11 trigger mount/pet/buff)
64-65  LE16  → model+752
66-97  8×LE32  equipment slot IDs ❓
98-101 LE32  equipment slot 13
102-105 LE32 equipment slot 14
106-107 LE16 extra count → callback
108-111 LE32 equipment slot 15
112-114 3B   state/buff bytes
115    u8    guild short-tag length
116-131 16B  guild short tag string
132-139 8B   trailing appearance/buff data
```

**Note**: Offset 62 is **faction**, not class_id. Our current builder accepts a `class_id` parameter but writes it to the faction byte — see [09_REVERSE_ENGINEERING.md](09_REVERSE_ENGINEERING.md) for the investigation. The real class_id field is only in the 0x0002 profile (init packet), not in spawn packets.

**Equipment slots**: `0x0001` carries 8 + 3 equipment IDs that drive the visible outfit. We currently send all zeros (no DB equipment table yet), which is why remote players appear as naked default models.

#### `0x000E` — Entity spawn (47B) ✅

A plain entity spawn. Client handler: `sub_5EF410`. No UI side effects, so it works mid-game on receivers that have already finished their init flow (unlike `0x0001` which bails when the client isn't in char-select state).

```
00-01  LE16  opcode = 0x000E
02-05  LE32  entity_id
06-09  LE32  flags
10-13  LE32  tile_x
14-17  LE32  tile_y
35     u8    direction
36-39  LE32  sprite_id (template, 999 = char-select player model)
40-41  LE16  → model+506
42-43  LE16  → model+508
```

Paired with `0x0001` in our presence broadcast so that both fresh-login and mid-game observers see new arrivals. See [04_WORLD_SERVER.md](04_WORLD_SERVER.md) for the dual-opcode story.

#### `0x0005` — Entity move (24B) ✅

Tells observers that an entity is walking from one position to another.

```
00-01  LE16  opcode = 0x0005
02-05  LE32  entity_id
06-09  LE32  cur_x
10-13  LE32  cur_y
14-17  LE32  dst_x
18-21  LE32  dst_y
22-23  LE16  speed
```

**Correction**: older docs called this 0x0018. That was wrong — 0x0018 is an emote/animation opcode. Entity move is 0x0005 (confirmed from `sub_5EC7B0`).

#### `0x006D` — Move response marker (2B) ✅

Single-opcode sub-message used as the **first** sub-message in a movement response. Pairs with an immediately-following `0x0005` to form the 30B move response:

```
sub1:  [LE16 sub_len=2][LE16 0x006D]
sub2:  [LE16 sub_len=24][0x0005 entity_move layout…]
```

#### `0x001B` — Entity despawn (14B) ✅

```
00-01  LE16  opcode = 0x001B
02-05  LE32  entity_id
06-07  LE16  flags
08-11  LE32  area_id
12-13  2B    zeros
```

#### `0x0002` — Player profile ✅ (mostly — see note)

Carried only in `init_pkt1`. This is the big "self" profile packet. Client handler: `sub_5E9C90`. Full byte layout is in [09_REVERSE_ENGINEERING.md](09_REVERSE_ENGINEERING.md); the short version:

```
00-01  LE16  opcode = 0x0002
02-05  LE32  entity_id
...
16-31  16B   name
60     u8    faction → model+744         (capture=3=Steel)
64     u8    → model+1206 ❓
65-68  LE32  level → model+588
69-92  6×LE32 stat block 1 → model+1208..+1228 ❓ big values, likely XP/timers
93     u8    class_id → model+1232       (capture=1=Priest)
102-113 LE32×3 HP_max / HP / MP_max
114-117 LE32  MP
118-121 WORD×2 stamina / stamina_max
126-157 5×LE32 combat stats (R.Atk/L.Atk/Dfs/Spl Atk/Spl Dfs) ✅ empirical
...
```

### NPCs, monsters, entities (init-time spawns) ❓

These come from the captured seed packets (`init_pkt1`, `area_pkt*`). Layouts are mostly verified by comparing bytes across captures; we don't always know what each field *means*, but we know what values to write to get the client to render something.

| Op | Size | What | Status |
|---|---|---|---|
| `0x0007` | 7B | Entity position marker | ❓ |
| `0x0008` | 65B | NPC spawn (name, pos, sprite, type) | ✅ layout, ❓ some extras |
| `0x000F` | 46B | Monster spawn (same shape as 0x000E + 1B) | ❓ |
| `0x0185` | 14B | Entity reference anchor | ❓ init-only |
| `0x0021` | 24B | Entity anchor (map_id carrier) | ❓ init-only |
| `0x005D` | 6B | Unix timestamp | ✅ |

### Chat & dialog

#### `0x001E` — Chat message / area entity ref ✅ (dual-use)

**Important**: `0x001E` means two different things depending on sub_len and context.

**Short form (6B)** — area entity reference, only seen inside init packets:
```
00-01  LE16  opcode = 0x001E
02-05  LE32  entity_ref
```

**Long form (variable)** — chat/system message, used at runtime:
```
00-01  LE16  opcode = 0x001E
02-03  LE16  chat_type (0x0001=system, 0x0017=player)
04-07  LE32  sender_entity_id
08-15  8B    sender_name (NUL-padded)
16-19  LE32  pos_x
20-23  LE32  pos_y
24     u8    channel (0x01=system, 0x00=normal)
25-..  var   message (NUL-terminated, max ~200 chars)
```

Our NPC-dialog workaround uses the long form with `chat_type=0x0001, channel=0x01` to get NPC speech into the chat bubble. ❓ The "real" NPC dialog opcode is unknown — our best guess was `0x002B` but we've never seen it actually work.

#### `0x0128` — World/shout/party chat (variable) ❓

```
00-01  LE16  opcode = 0x0128
02-05  LE32  source_entity_id
06     u8    channel (0x0A=world, 0x0B=shout, 0x02=party)
07-..  var   sender_name (NUL-terminated)
..-..  var   message (NUL-terminated)
```

Implemented in `packet_builders.build_world_chat`. Not currently broadcast by our handlers — chat uses `0x001E` instead.

### Combat

#### `0x0019` — Combat action (27-53B) ❓

```
00-01  LE16  opcode = 0x0019
02-05  LE32  source_entity
06-09  LE32  target_entity
10-11  LE16  action_type (1=basic, 2=skill, 3=miss)
12-13  LE16  skill_id
14-17  LE32  damage
18-19  LE16  flags (crit=0x01, miss=0x02)
20-24  5B    zeros
```

The client renders a hit animation + damage number when it receives this. ❌ HP bar updates don't use 0x0019 — we've been sending it for combat and HP displays don't move. The real HP-update opcode is still unidentified. See [09_REVERSE_ENGINEERING.md](09_REVERSE_ENGINEERING.md).

#### `0x000B` — Entity status (13B) ❓

```
00-01  LE16  opcode = 0x000B
02-05  LE32  entity_id
06     u8    status_a (1=alive, 7=damaged, 0=dead)
07     u8    status_b
08-12  5B    zeros
```

### Stats & currency

#### `0x0042` — Character stats (107B) ❓

Core character-sheet packet. First 18 bytes are verified; the 89B tail is replayed from capture.

```
00-01  LE16  opcode = 0x0042
02-05  LE32  hp
06-09  LE32  hp_max
10-13  LE32  mp
14-17  LE32  mp_max
18-106 89B   base stats / resistances / weight / etc. ❓ replayed from capture
```

#### `0x0149` — Currency (38B) ✅

```
00-01  LE16  opcode = 0x0149
02-05  LE32  currency_type
06-09  4B    zeros
10-13  LE32  amount
14-33  20B   zeros
34     u8    flag
35-36  LE16  tail
37     u8    zero
```

### Keepalive & infrastructure

#### `0x018A` — Keepalive (10B or 14B) ✅

```
Tick (10B, every 1s):  [0x018A][LE32 type=4][LE32 0]
Timer (14B, every 60s): [0x018A][LE32 type=8][LE32 minute_count][LE32 0]
```

#### `0x000C` — MOTD (variable) ✅

Login-response only.

```
00-01  LE16  opcode = 0x000C
02-05  LE32  flags (0x69402978)
06-09  LE32  text_length
10-..  var   text (NUL-terminated)
```

### Settings / zoning / world config

All of these ship from seed packets with fields replayed from capture. We know just enough to not break them:

| Op | Size | Role | Status |
|---|---|---|---|
| `0x0013` | 10B | Pet status tick | ❓ |
| `0x0014` | 9B | Flag / mode | ❓ |
| `0x0015` | 7B | Server timer | ❓ |
| `0x001D` | 16B | Entity setting (carries player eid in ACK response) | ❓ |
| `0x001F` | variable | Player title ("PT" prefix) | ❓ |
| `0x0022` | 24B | Loot announce | ❓ |
| `0x0027` | 10B | Area ref | ❓ sent in ACK response |
| `0x0028` | 85B | Player appears (older spawn variant) | ❓ |
| `0x003A` | 19B | Inspect player response | ❓ |
| `0x003F`, `0x0040` | 10B | Area refs | ❓ sent in ACK response |
| `0x005B` | 218B | Slot table (24 × 9B) | ❓ init |
| `0x005C` | 39B | Empty padding | ❓ |
| `0x0063` | 8B | Equip result | ❓ |
| `0x006A` | 108-134B | Buff info | ❓ |
| `0x012A` | 23B | Indexed slot | ❓ |
| `0x0142` | 3B | Toggle flag | ❓ |
| `0x0144 / 0x0145 / 0x0162 / 0x0164 / 0x017D` | small | Zero-filled init constants | ❓ |
| `0x014A` | 19B | Party name | ❓ |
| `0x0158` | 74B | Skill slot (34× in init_pkt4) | ❓ |
| `0x0160` | 27B | Settings block | ❓ |
| `0x016F` | 20B | Flags | ❓ |
| `0x0178` | 10B | Two-value | ❓ |
| `0x017A` | 19B | Data | ❓ |
| `0x018E` | 166B | Zone list (20 × 8B) | ❓ |
| `0x018F` | 14B | Three-value | ❓ |
| `0x0191` | 10B | Session config | ❓ |

---

## C→S (client → server)

Every C→S sub-message has `[LE16 sub_len][LE16 opcode][body…]`. Handler implementations live in [src/handlers/](../src/handlers/).

| Op | Name | Size | Body | Handler | Status |
|---|---|---|---|---|---|
| `0x0003` | ACK | 4B | — | inline in world loop | ✅ |
| `0x0004` | MOVEMENT_REQ | 15B | `[7B from/flags][LE16 dst_x@+11][LE16 dst_y@+13]` | `movement.handle_movement` | ✅ |
| `0x0005` | ENTITY_POS | 8B | `[LE32 entity_id]` | → `npc.handle_entity_action` | ✅ |
| `0x0006` | ENTITY_SELECT | 8B | `[LE32 target@+4]` | `misc.handle_entity_select` | ✅ |
| `0x0009` | STOP_ACTION | 4B | — | `combat.handle_stop_action` (clears dialog) | ✅ |
| `0x000D` | ENTITY_ACTION | 8B | `[LE32 runtime_entity@+4]` | `npc.handle_entity_action` | ✅ |
| `0x000F` | TARGET_MOB | 8B | `[LE32 mob_id@+4]` | `combat.handle_target_mob` | ✅ |
| `0x0012` | BUY_SELL | 8B | `[LE16 item@+4][LE16 qty@+6]` | `misc.handle_buy_sell` (stub) | ❓ |
| `0x0016` | USE_SKILL | 9B | `[u8 skill_id@+4][LE32 target@+5]` | `combat.handle_use_skill` | ✅ |
| `0x0019` | ENTITY_ACTION_2 | 8B | same shape as `0x000D` | → `npc.handle_entity_action` | ✅ |
| `0x001A` | REQUEST_PLAYER_DETAILS | 8B | `[LE32 target@+4]` | `social.handle_request_player_details` (log only) | ✅ |
| `0x002C` | INSPECT_PLAYER | 8B | `[LE32 target@+4]` | not implemented | ❌ |
| `0x002E` | CHAT_SEND | 5B+ | `[u8 channel][NUL-term text]` | `social.handle_chat_send` | ✅ |
| `0x003E` | TOGGLE_ACTION | 5B | `[u8 action_id]` | `misc.handle_toggle_action` | ✅ |
| `0x0044` | NPC_DIALOG | 9B | `[4B unk][u8 option_idx@+8]` | `npc.handle_npc_dialog` | ✅ |
| `0x0143` | ZONE_READY | 4B | — | `misc.handle_zone_ready` | ✅ |
| `0x0150` | EMOTE | 6B | `[LE16 emote_id]` | `social.handle_emote` (log only) | ✅ |
| `0x0152` | ANTI_AFK_TICK | 4B | — | silent | ✅ |
| `0x015E` | PING | 5B | — | silent | ✅ |

### Removed from earlier docs

- `0x0034` CANCEL_ACTION — **never existed**. Older docs claimed it as an alias for STOP_ACTION, but the dispatch table has no such opcode and the client doesn't send it. Dialog cancellation is just `0x0009` STOP_ACTION.

### Known unknowns

- ❌ **NPC dialog opcode (S→C)** — we fake it with `0x001E` chat. The real opcode hasn't been identified. Our earlier guess of `0x002B` never verified.
- ❌ **HP-bar update opcode** — `0x0019` (combat action) renders damage numbers but doesn't move the target's health bar. We've been wrong about which opcode updates target HP at least twice now. Needs a targeted capture of live combat.
- ❌ **The "3 area refs" in the ACK response** — `0x0027 / 0x003F / 0x0040` are needed for the client to proceed past world init but their field meanings are guessed.
- ❌ **Equipment packets** — we have a stub `0x0063` equip result but we've never sent one and the client has no equipment to equip anyway. See [07_DATABASE.md](07_DATABASE.md) for the missing schema.

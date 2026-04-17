"""Shared sub-message builder functions for Angels Online packets.

Used by both world_init_data.py (init packets) and area_entity_data.py
(area packets) to construct sub-messages from database rows.

Sub-message format: [LE16 sub_len][sub_data] repeated within payloads.

Builder categories:
  - Existing: 0x0005, 0x0008, 0x000E, 0x000F, 0x001D (area entity types)
  - Entity:   0x0007 (entity position marker)
  - Stats:    0x0042 (character stats)
  - Slots:    0x0158 (skill/hotbar), 0x005B (slot table)
  - Names:    0x014A (party/event name)
  - Area:     0x0027, 0x003F, 0x0040 (area references)
  - Player:   0x0185 (entity ref), 0x0021 (anchor)
  - Constants: 0x005C, 0x0144, 0x0145, 0x0162, 0x0164, 0x017D (zero-filled)
  - Small:    0x0014, 0x001E, 0x005D, 0x012A, 0x0142, 0x0149, 0x0160,
              0x016F, 0x0178, 0x017A, 0x018A, 0x018F
  - Session:  0x018E (zone list), 0x0191 (session config)
  - Spawn:    0x0001 (player spawn, raw builder)
  - Gameplay: 0x000A (keepalive tick/timer), 0x001B (entity despawn),
              0x0015 (server timer), 0x0020 (redirect), 0x0022 (loot announce),
              0x003A (inspect player resp), 0x0063 (equip result),
              movement resp (0x006D + 0x0005 framed), ack response cluster
"""

import os
import struct


# =============================================================================
# Utility functions
# =============================================================================

def pack_sub(data: bytes) -> bytes:
    """Wrap a sub-message with its LE16 length prefix."""
    return struct.pack('<H', len(data)) + data


def assemble_payload(sub_messages: list[bytes]) -> bytes:
    """Concatenate sub-messages with LE16 length prefixes."""
    parts = []
    for msg in sub_messages:
        parts.append(struct.pack('<H', len(msg)))
        parts.append(msg)
    return b''.join(parts)


def pad_name(name: str, length: int) -> bytes:
    """Encode a name string into a fixed-length null-terminated byte field."""
    encoded = name.encode('utf-8')[:length - 1]
    return encoded + b'\x00' * (length - len(encoded))


# =============================================================================
# Area entity builders (existing) — used by seed scripts and DB-based building
# =============================================================================

def build_setting_16(entity_id: int, marker: int, setting_id: int,
                     value_lo: int, value: int, value_hi: int) -> bytes:
    """Build a 16-byte 0x001D entity setting sub-message."""
    eid_lo = entity_id & 0xFFFF
    eid_hi = (entity_id >> 16) & 0xFFFF
    return struct.pack('<HHHHHHHH',
                       0x001D, eid_lo, eid_hi, marker, setting_id,
                       value_lo, value, value_hi)


def build_npc(row) -> bytes:
    """Build a 65-byte 0x0008 NPC sub-message from a DB row."""
    return struct.pack('<HHH', 0x0008, row["npc_id_lo"], row["npc_id_hi"]) + \
           struct.pack('<I', row["unk1"]) + \
           struct.pack('<II', row["pos_x"], row["pos_y"]) + \
           pad_name(row["name"], 13) + \
           bytes(row["extra"])


def build_npc_spawn(runtime_entity_id: int, tile_x: int, tile_y: int,
                    name: str, sprite_id: int, npc_type_id: int,
                    is_monster: bool = False) -> bytes:
    """Build a 65-byte 0x0008 spawn sub-message from map entity data.

    Used for both friendly NPCs and monsters (confirmed: real server uses
    0x0008 for both; 0x000F is scenery only).

    Format (65 bytes):
      [2]  opcode = 0x0008
      [2]  entity_id low word
      [2]  entity_id high word
      [4]  unk1: 1 = friendly NPC, 0 = monster
      [4]  tile_x
      [4]  tile_y
      [13] name (null-padded ASCII)
      [34] extra (differs between NPC and monster):

    NPC extra:
      [4]=0x05  [5:7]=sprite_id  [11]=0xC8  [16:18]=npc_type_id

    Monster extra (from pcap):
      [3]=0x01  [5:7]=sprite_id  [9:11]=LE16(7)  [11]=0x01
      [15]=0x04  [16:18]=monster_type_id  [18:20]=0x8008
    """
    eid_lo = runtime_entity_id & 0xFFFF
    eid_hi = (runtime_entity_id >> 16) & 0xFFFF

    extra = bytearray(34)
    struct.pack_into('<H', extra, 5, sprite_id)
    struct.pack_into('<H', extra, 16, npc_type_id)

    if is_monster:
        extra[3] = 0x01
        struct.pack_into('<H', extra, 9, 7)
        extra[11] = 0x01
        extra[15] = 0x04
        struct.pack_into('<H', extra, 18, 0x8008)
        unk1 = 0
    else:
        extra[4] = 0x05
        extra[11] = 0xC8
        unk1 = 1

    return (struct.pack('<HHH', 0x0008, eid_lo, eid_hi)
            + struct.pack('<I', unk1)
            + struct.pack('<II', tile_x, tile_y)
            + pad_name(name, 13)
            + bytes(extra))


def build_entity(row) -> bytes:
    """Build a 45-byte 0x000E entity spawn sub-message from a DB row."""
    return struct.pack('<HHH', 0x000E, row["entity_id_lo"], row["entity_id_hi"]) + \
           struct.pack('<I', row["unk1"]) + \
           struct.pack('<II', row["pos_x"], row["pos_y"]) + \
           bytes(row["name_bytes"]) + bytes(row["tail_bytes"])


def build_mob(row) -> bytes:
    """Build a 46-byte 0x000F mob spawn sub-message from a DB row."""
    return struct.pack('<HHH', 0x000F, row["mob_id_lo"], row["mob_id_hi"]) + \
           struct.pack('<I', row["unk1"]) + \
           struct.pack('<II', row["pos_x"], row["pos_y"]) + \
           bytes(row["name_bytes"]) + bytes(row["tail_bytes"])


def build_position(row) -> bytes:
    """Build a 24-byte 0x0005 position sub-message from a DB row."""
    return struct.pack('<HHH', 0x0005, row["entity_id_lo"], row["entity_id_hi"]) + \
           struct.pack('<IIII', row["x1"], row["y1"], row["x2"], row["y2"]) + \
           struct.pack('<H', row["speed"])


# =============================================================================
# Entity position (0x0007) — 7 bytes, 82 instances in area packets
# =============================================================================

def build_entity_pos(entity_id_lo: int, entity_id_hi: int,
                     flag: int = 0) -> bytes:
    """Build a 7-byte 0x0007 entity position marker.

    Format: [LE16 opcode][LE16 entity_lo][LE16 entity_hi][B flag]
    Used to mark entity positions without movement data.
    """
    return struct.pack('<HHHB', 0x0007, entity_id_lo, entity_id_hi, flag)


# =============================================================================
# Character stats (0x0042) — 107 bytes
# =============================================================================

def build_char_stats(hp: int, hp_max: int, mp: int, mp_max: int,
                     stats_tail: bytes) -> bytes:
    """Build a 107-byte 0x0042 character stats block.

    Format: [LE16 opcode][LE32 hp][LE32 hp_max][LE32 mp][LE32 mp_max][89B stats]
    The 89-byte stats tail contains base stats, resistances, weight etc.
    """
    return struct.pack('<HIIII', 0x0042, hp, hp_max, mp, mp_max) + stats_tail


# =============================================================================
# Skill/hotbar slot (0x0158) — 74 bytes, 34 instances in init_pkt4
# =============================================================================

def build_skill_slot(slot_data: bytes) -> bytes:
    """Build a 74-byte 0x0158 skill/hotbar slot.

    Format: [LE16 opcode][72B slot data]
    Each of the 34 slots contains skill IDs, levels, and parameters.
    """
    return struct.pack('<H', 0x0158) + slot_data


# =============================================================================
# Party/event name (0x014A) — 19 bytes
# =============================================================================

def build_party_name(name_bytes: bytes, tail: bytes) -> bytes:
    """Build a 19-byte 0x014A party/event name.

    Format: [LE16 opcode][12B name null-terminated][5B tail]
    The name field is 12 bytes with null terminator.
    Tail contains event flags/parameters.
    """
    return struct.pack('<H', 0x014A) + name_bytes + tail


# =============================================================================
# Area reference (0x0027, 0x003F, 0x0040) — 10 bytes each
# =============================================================================

def build_area_ref(opcode: int, area_id: int) -> bytes:
    """Build a 10-byte area reference packet.

    Format: [LE16 opcode][LE32 area_id][LE32 0]
    Used by opcodes 0x0027, 0x003F, 0x0040 which share the same structure.
    area_id is typically 0x0020A0C3 for the starting zone.
    """
    return struct.pack('<HII', opcode, area_id, 0)


# =============================================================================
# Entity reference (0x0185) — 14 bytes
# =============================================================================

def build_entity_ref_0185(entity_id: int) -> bytes:
    """Build a 14-byte 0x0185 entity reference.

    Format: [LE16 opcode][LE32 entity_id][8B zeros]
    References the player's entity ID.
    """
    return struct.pack('<HI', 0x0185, entity_id) + b'\x00' * 8


# =============================================================================
# Slot table (0x005B) — 218 bytes
# =============================================================================

def build_slot_table(entries: list[tuple[int, int]]) -> bytes:
    """Build a 218-byte 0x005B slot table.

    Format: [LE16 opcode][24 x 9-byte slots]
    Each slot: [B active_flag][B index][7B zeros]
    entries: list of (active_flag, index) for populated slots.
    """
    data = bytearray(216)  # 24 slots x 9 bytes = 216
    for i, (flag, index) in enumerate(entries):
        offset = i * 9
        data[offset] = flag
        data[offset + 1] = index
    return struct.pack('<H', 0x005B) + bytes(data)


# =============================================================================
# Timestamp (0x005D) — 6 bytes
# =============================================================================

def build_timestamp_005D(timestamp: int) -> bytes:
    """Build a 6-byte 0x005D timestamp.

    Format: [LE16 opcode][LE32 timestamp]
    """
    return struct.pack('<HI', 0x005D, timestamp)


# =============================================================================
# Indexed slot (0x012A) — 23 bytes
# =============================================================================

def build_indexed_slot_012A(index: int) -> bytes:
    """Build a 23-byte 0x012A indexed slot.

    Format: [LE16 opcode][LE32 index][17B zeros]
    Two instances exist: index=0 and index=1.
    """
    return struct.pack('<HI', 0x012A, index) + b'\x00' * 17


# =============================================================================
# Toggle flag (0x0142) — 3 bytes
# =============================================================================

def build_toggle_0142(flag: int = 0) -> bytes:
    """Build a 3-byte 0x0142 toggle flag.

    Format: [LE16 opcode][B flag]
    Appears twice in init_pkt3, both with flag=0.
    """
    return struct.pack('<HB', 0x0142, flag)


# =============================================================================
# Three-value packet (0x018F) — 14 bytes
# =============================================================================

def build_triple_018F(a: int, b: int, c: int) -> bytes:
    """Build a 14-byte 0x018F three-value packet.

    Format: [LE16 opcode][LE32 a][LE32 b][LE32 c]
    Captured values: a=3, b=83, c=3.
    """
    return struct.pack('<HIII', 0x018F, a, b, c)


# =============================================================================
# Two-value packet (0x0178) — 10 bytes
# =============================================================================

def build_pair_0178(value: int, trailing: int = 0) -> bytes:
    """Build a 10-byte 0x0178 two-value packet.

    Format: [LE16 opcode][LE32 value][LE32 trailing]
    Captured values: value=4, trailing=0.
    """
    return struct.pack('<HII', 0x0178, value, trailing)


# =============================================================================
# Zero-filled constant packets
# =============================================================================

def build_empty_005C() -> bytes:
    """Build a 39-byte 0x005C (opcode + 37 zero bytes)."""
    return struct.pack('<H', 0x005C) + b'\x00' * 37


def build_empty_0144() -> bytes:
    """Build an 8-byte 0x0144 (opcode + 6 zero bytes)."""
    return struct.pack('<H', 0x0144) + b'\x00' * 6


def build_empty_0145() -> bytes:
    """Build a 4-byte 0x0145 (opcode + 2 zero bytes)."""
    return struct.pack('<H', 0x0145) + b'\x00' * 2


def build_empty_0162() -> bytes:
    """Build a 10-byte 0x0162 (opcode + 8 zero bytes)."""
    return struct.pack('<H', 0x0162) + b'\x00' * 8


def build_empty_0164() -> bytes:
    """Build a 19-byte 0x0164 (opcode + 17 zero bytes)."""
    return struct.pack('<H', 0x0164) + b'\x00' * 17


def build_empty_017D() -> bytes:
    """Build a 4-byte 0x017D (opcode + 2 zero bytes)."""
    return struct.pack('<H', 0x017D) + b'\x00' * 2


# =============================================================================
# Small data packets (partially decoded, take raw payload after opcode)
# =============================================================================

def build_flag_0014(value: int = 1, mode: int = 8,
                    type_a: int = 4, type_b: int = 1) -> bytes:
    """Build a 9-byte 0x0014 flag/mode packet.

    Format: [LE16 opcode][LE32 value][B mode][B type_a][B type_b]
    Captured values: value=1, mode=8, type_a=4, type_b=1.
    """
    return struct.pack('<HIBBB', 0x0014, value, mode, type_a, type_b)


def build_entity_001E(entity_ref: int = 0x306FDCD8) -> bytes:
    """Build a 6-byte 0x001E area entity reference.

    Format: [LE16 opcode][LE32 entity_ref]
    References an entity in the current area. Player-specific.
    """
    return struct.pack('<HI', 0x001E, entity_ref)


def build_anchor_0021(index: int = 1, area_id: int = 0x0020A0C3,
                      map_id: int = 101, tail: bytes = b'') -> bytes:
    """Build a 24-byte 0x0021 entity anchor.

    Format: [LE16 opcode][LE32 index][LE32 area_id][LE32 map_id]
            [6B zeros][LE16 0x00][B 0xC3][B 0xA0][B 0x01]
    Anchors the player entity to a zone/area.
    """
    # Build the 22-byte body using the captured layout
    body = struct.pack('<III', index, area_id, map_id)
    body += b'\x00' * 6
    # Last 4 bytes from capture: 00 c3 a0 01
    body += bytes([0x00, 0xC3, 0xA0, 0x01])
    return struct.pack('<H', 0x0021) + body


def build_currency_0149(currency_type: int = 1, amount: int = 500,
                        flag: int = 5, tail_hi: int = 0x00FFFF) -> bytes:
    """Build a 38-byte 0x0149 currency/value packet.

    Format: [LE16 opcode][LE32 type][LE32 0][LE32 amount]
            [20B zeros][B flag][LE16 tail_hi][B 0x00]
    Captured: type=1, amount=500 (starting gold).
    """
    data = struct.pack('<III', currency_type, 0, amount)
    data += b'\x00' * 20
    data += struct.pack('<B', flag)
    data += struct.pack('<H', tail_hi)
    data += b'\x00'
    return struct.pack('<H', 0x0149) + data


def build_settings_0160(flag: int = 1, val_a: int = 5,
                        val_b: int = 5, val_c: int = 5) -> bytes:
    """Build a 27-byte 0x0160 settings packet.

    Format: [LE16 opcode][LE32 flag][9B zeros][LE32 a][LE32 b][LE32 c]
    Captured values: flag=1, a=b=c=5.
    """
    return struct.pack('<HI', 0x0160, flag) + b'\x00' * 9 + \
           struct.pack('<III', val_a, val_b, val_c)


def build_flags_016F(type_id: int = 5, flag_a: int = 1, flag_b: int = 1,
                     flag_c: int = 1) -> bytes:
    """Build a 20-byte 0x016F flags packet.

    Format: [LE16 opcode][LE32 type][B flag_a][B flag_b][3B zeros]
            [B flag_c][8B zeros]
    Captured: type=5, flags a=1, b=1, c=1.
    """
    data = struct.pack('<I', type_id)
    data += struct.pack('BB', flag_a, flag_b)
    data += b'\x00' * 3
    data += struct.pack('B', flag_c)
    data += b'\x00' * 8
    return struct.pack('<H', 0x016F) + data


def build_data_017A(val_a: int = 4, val_b: int = 1) -> bytes:
    """Build a 19-byte 0x017A data packet.

    Format: [LE16 opcode][LE32 val_a][LE32 val_b][9B zeros]
    Captured values: a=4, b=1.
    """
    return struct.pack('<HII', 0x017A, val_a, val_b) + b'\x00' * 9


def build_data_018A(val_a: int = 8, val_b: int = 18,
                    val_c: int = 1, val_d: int = 1) -> bytes:
    """Build a 23-byte 0x018A init data packet.

    Format: [LE16 opcode][LE32 a][LE32 b][LE32 c][LE32 d][5B zeros]
    Captured: a=8, b=18(class_id?), c=1, d=1.
    Not to be confused with the 10/14-byte 0x018A keepalive tick.
    """
    return struct.pack('<HIIII', 0x018A, val_a, val_b, val_c, val_d) + \
           b'\x00' * 5


# =============================================================================
# Zone list (0x018E) — 166 bytes
# Sent as the very first S->C packet when a game session opens,
# before 0x018D (init header). Lists available zone IDs on the server.
# =============================================================================

def build_zone_list(zone_ids: list[int], num_slots: int = 20) -> bytes:
    """Build a 166-byte 0x018E zone capability list.

    Format: [LE16 opcode][LE32 unk=1][num_slots x (LE32 zone_id)(LE32 zero)]
    Captured zones (10 of 20 slots used):
      Slot 0-2:  0x3C, 0x3D, 0x3E
      Slot 3-9:  empty (zeros)
      Slot 10-16: 0x29, 0x2A, 0x2D, 0x32, 0x33, 0x35, 0x38
      Slot 17-19: empty (zeros)
    """
    data = bytearray(num_slots * 8)
    for i, zone_id in enumerate(zone_ids[:num_slots]):
        struct.pack_into('<II', data, i * 8, zone_id, 0)
    return struct.pack('<HI', 0x018E, 1) + bytes(data)


# Captured zone list from live server (Hestia server 2, version 8.5.0.3).
# Zone IDs in slot order: first 3 slots = 0x3C/0x3D/0x3E, then gap,
# then 0x29/0x2A/0x2D/0x32/0x33/0x35/0x38, then trailing zeros.
_ZONE_LIST_SLOTS = [
    0x3C, 0x3D, 0x3E, 0, 0, 0, 0, 0, 0, 0,
    0x29, 0x2A, 0x2D, 0x32, 0x33, 0x35, 0x38, 0, 0, 0,
]

def build_zone_list_captured() -> bytes:
    """Build the captured 166-byte zone list verbatim."""
    return build_zone_list(_ZONE_LIST_SLOTS)


# =============================================================================
# Session config (0x0191) — 10 bytes
# Sent during init sequence; contains two LE32 values (0 and 50).
# Likely a session timeout or tick configuration.
# =============================================================================

def build_session_config(a: int = 0, b: int = 50) -> bytes:
    """Build a 10-byte 0x0191 session config.

    Format: [LE16 opcode][LE32 a][LE32 b]
    Captured values: a=0, b=50 (possibly tick interval in ms).
    """
    return struct.pack('<HII', 0x0191, a, b)


# =============================================================================
# Player spawn (0x0001) — variable (134B or 182B)
# Sent for each other player visible in the starting zone.
# Contains: entity_id, level, class, name (null-term), guild tag (null-term),
#           skill/buff data, guild name (length-prefixed), and a tail.
# Raw builder — full structure needs more RE work.
# =============================================================================

def build_player_spawn(data: bytes) -> bytes:
    """Build a 0x0001 player spawn packet.

    Format: [LE16 opcode][variable data]
    The data blob encodes entity_id, level, class_id, name string,
    guild tag string, skill references, guild name, and misc tail bytes.
    Typical sizes: 134B or 182B (134B without guild name extension).
    """
    return struct.pack('<H', 0x0001) + data


def build_remote_player_spawn_000E(
    entity_id: int,
    tile_x: int,
    tile_y: int,
    sprite_id: int = 999,
    direction: int = 0,
) -> bytes:
    """Build a 0x000E ENTITY_SPAWN sub-message.

    Parsed by client sub_5EF410. Creates an entity via sub_5B85D0 which
    passes data[34..37] as the sprite/template ID to sub_568DD0 →
    sub_75F3F0 (entity template lookup). Unlike sub_5E97F0 (opcode
    0x0001), this handler has no UI side effects and no state checks
    — it's safe to send mid-game to already-initialized clients.

    Using `sprite_id=999` creates the same entity template type that
    sub_5E97F0 uses internally for remote players (via sub_5B8430 →
    hardcoded type 999 for char-select preview model). This avoids
    the "char-select context required" limitation of 0x0001 while
    keeping the player model.

    Layout (47 bytes, size derived from sub_5EF410 field reads):
      [0-1]    opcode 0x000E
      [2-5]    entity_id LE32
      [6-9]    flags LE32
      [10-13]  tile_x LE32
      [14-17]  tile_y LE32
      [35]     direction byte (<< 21 → facing)
      [36-39]  sprite_id LE32 (template for sub_75F3F0)
      [40-41]  WORD → model+506
      [42-43]  WORD → model+508
      [44]     byte → sub_564FE0
      [45]     byte trailing
    """
    buf = bytearray(47)
    struct.pack_into('<H', buf, 0, 0x000E)
    struct.pack_into('<I', buf, 2, entity_id)
    # data[4] flags (leave 0)
    struct.pack_into('<I', buf, 10, tile_x)
    struct.pack_into('<I', buf, 14, tile_y)
    buf[35] = direction & 0xFF
    struct.pack_into('<I', buf, 36, sprite_id)
    # Leave offsets 40..46 zero
    return bytes(buf)


def build_remote_player_spawn_0008(
    entity_id: int,
    tile_x: int,
    tile_y: int,
    player_name: str,
    appearance: tuple = (0, 0, 0, 0, 0),
    class_id: int = 0,
    level: int = 1,
    direction: int = 0,
) -> bytes:
    """Build a 0x0008 NPC_SPAWN sub-message in PLAYER mode.

    Parsed by client sub_5EBBF0. When the WORD at data[45] is 6269 or
    6270 (male/female player sprite IDs), the client routes the extra
    appearance/equipment block at data[63] through sub_5EC200, which
    populates the entity's appearance bytes (model+576..+580) and
    equipment slots. This is the packet the real server uses for
    "remote player enters view" mid-game, NOT opcode 0x0001 (which is
    a login-only self-init handler that relies on char-select state
    that's discarded once the world loop starts).

    Layout (112 bytes total):
      [0-1]    opcode 0x0008
      [2-5]    entity_id LE32
      [6-9]    flags → model+480 (0 is fine)
      [10-13]  tile_x LE32
      [14-17]  tile_y LE32
      [18-33]  character name (16 bytes null-padded)
      [35]     direction byte (v18+35 << 21 → facing)
      [36-39]  LE32 passed to sub_5B85D0 as type pattern
      [40]     class/faction byte → model+744
      [42-45]  level LE32 → model+588
      [46]     byte → model+592
      [47-48]  WORD = 6269 (male) or 6270 (female) → model+484
               THIS is the flag that makes the client treat the entity
               as a player and parse the appendix.
      [49-50]  WORD → model+506
      [51-52]  WORD → model+508
      [53-54]  WORD → model+752
      [55-58]  LE32 → model+996
      [59-62]  LE32 → sub_4B94A0
      [63]     byte → sub_4B94E0
      [65..]   appearance/equipment block (appendix), parsed by sub_5EC200:
        [65]   appearance byte 1 → model+576
        [66]   appearance byte 5 → model+580 (note reordering)
        [67]   appearance byte 4 → model+579
        [68]   appearance byte 3 → model+578
        [69]   appearance byte 2 → model+577
        [70-101] 8 × LE32 equipment/skill slot IDs
        [102-105] LE32 slot 13
        [106-109] LE32 slot 14
        [110-111] WORD → model+520 (anim/stage)
    """
    buf = bytearray(112)
    struct.pack_into('<H', buf, 0, 0x0008)
    struct.pack_into('<I', buf, 2, entity_id)
    # data[4] flags → model+480 (leave 0)
    struct.pack_into('<I', buf, 10, tile_x)
    struct.pack_into('<I', buf, 14, tile_y)
    # Name (16 bytes null-padded) at data[16]
    name_bytes = player_name.encode('ascii', errors='replace')[:16]
    buf[18:18 + len(name_bytes)] = name_bytes
    # Direction at data[33]
    buf[35] = direction & 0xFF
    # data[34..37] is the MODEL TEMPLATE ID passed to sub_568DD0 via
    # sub_5B85D0. For players this must be 6269 (male) or 6270 (female)
    # — the player model template IDs. Using a bogus value (0 or an NPC
    # ID) makes sub_75F3F0 return NULL and the entity is never created.
    struct.pack_into('<I', buf, 36, 6269)
    # Class at data[38]
    buf[40] = class_id & 0xFF
    # Level LE32 at data[40..43]
    struct.pack_into('<I', buf, 42, level)
    # data[45] player marker WORD → same value tells sub_5EBBF0 this
    # is a player and to parse the appendix at data[63] via sub_5EC200
    struct.pack_into('<H', buf, 47, 6269)
    # Appearance block at data[63..] = buf[65..]
    # sub_5EC200 reads in odd order: [0]=app1, [1]=app5, [2]=app4, [3]=app3, [4]=app2
    buf[65] = appearance[0] & 0xFF  # app 1
    buf[66] = appearance[4] & 0xFF  # app 5
    buf[67] = appearance[3] & 0xFF  # app 4
    buf[68] = appearance[2] & 0xFF  # app 3
    buf[69] = appearance[1] & 0xFF  # app 2
    # Equipment slots 0-7 at buf[70..101] (8 × LE32) — leave zero
    # Slot 13 at buf[102..105], slot 14 at buf[106..109] — leave zero
    # Anim WORD at buf[110..111] — leave zero
    return bytes(buf)


def build_remote_player_spawn(
    entity_id: int,
    tile_x: int,
    tile_y: int,
    player_name: str,
    appearance: tuple = (0, 0, 0, 0, 0),
    class_id: int = 0,
    level: int = 1,
    guild_name: str = "",
    direction: int = 0,
    equipment: tuple = (0,) * 11,
) -> bytes:
    """Build a 0x0001 REMOTE_PLAYER_SPAWN sub-message.

    Parsed by the client at sub_5E97F0 (slot 1 of the world dispatch
    table set up in sub_5E5350). Used to make another player's model
    appear in the local player's view.

    Layout (140 bytes total, starts with opcode):
      [0-1]    opcode 0x0001
      [2-5]    entity_id (LE32) — sub_5B8430(eid, 0) creates/resets
      [6-9]    model+480 (captured: 0x8D)
      [10-13]  tile_x (LE32) — NB: TILE coordinates, not pixel. The
               client multiplies by tile size internally via sub_5B86F0.
      [14-17]  tile_y (LE32)
      [18-33]  character name (16 bytes null-padded)
      [35-49]  guild name (null-terminated string)
      [52]     facing direction byte (<< 21 → bearing angle)
      [53-56]  model+572 (LE32 flags)
      [57]     appearance byte 1 → model+576
      [58]     appearance byte 2 → model+577
      [59]     appearance byte 3 → model+578
      [60]     appearance byte 4 → model+579
      [61]     appearance byte 5 → model+580
      [62]     faction byte → model+744 (NOT class_id; capture=3=Steel for
               Soualz — matches profile 0x0002 at offset 60. The real
               class_id lives at profile offset 93, which 0x0001 does
               NOT carry. Remote players render without job info.)
      [63]     state byte → model+958 (7-11 trigger mount/pet/buff)
      [64-65]  WORD → model+752
      [66-97]  8 × LE32 equipment slot IDs
      [98-101] LE32 equip slot 13
      [102-105]LE32 equip slot 14
      [106-107]WORD extra
      [108-111]LE32 equip slot 15
      [112-114]state/buff bytes
      [115]    guild tag length
      [116-131]guild short tag string
      [132-139]trailing appearance/buff data

    Caller MUST pass position in tile coordinates. For pixel-based
    positions from movement handlers, divide by 32 (tile size) first.
    """
    buf = bytearray(140)
    struct.pack_into('<H', buf, 0, 0x0001)               # opcode
    struct.pack_into('<I', buf, 2, entity_id)
    struct.pack_into('<I', buf, 6, 0x8D)                 # model+480 (stable)
    struct.pack_into('<I', buf, 10, tile_x)              # tile X
    struct.pack_into('<I', buf, 14, tile_y)              # tile Y
    # Name at offset 18 (16 bytes null-padded)
    name_bytes = player_name.encode('ascii', errors='replace')[:16]
    buf[18:18 + len(name_bytes)] = name_bytes
    # Guild name at offset 35
    if guild_name:
        gn = guild_name.encode('ascii', errors='replace')[:14]
        buf[35:35 + len(gn)] = gn
    buf[52] = direction & 0xFF
    # Appearance bytes 55-59 in profile → 57-61 here (+2 offset because
    # sub_5E97F0 reads at a2+57 which is data[55])
    for i in range(5):
        buf[57 + i] = appearance[i] & 0xFF
    buf[62] = class_id & 0xFF
    # data[63] state byte — 0 = normal. Do NOT set to 7-11 or the client
    # triggers mount/pet/buff special handling.
    buf[63] = 0
    # Equipment slots — slots 0..7 at 66-97, slot 13 at 98, 14 at 102,
    # 15 at 108. Each is LE32 of an item_id. sub_5E97F0 passes these
    # through sub_4A4940 which drives the visible outfit.
    eq = tuple(equipment) + (0,) * max(0, 11 - len(equipment))
    for i in range(8):
        struct.pack_into('<I', buf, 66 + 4 * i, eq[i] & 0xFFFFFFFF)
    struct.pack_into('<I', buf, 98,  eq[8]  & 0xFFFFFFFF)
    struct.pack_into('<I', buf, 102, eq[9]  & 0xFFFFFFFF)
    struct.pack_into('<I', buf, 108, eq[10] & 0xFFFFFFFF)
    return bytes(buf)


# =============================================================================
# Gameplay S->C builders — confirmed from relay captures (Desktop/captures/)
# =============================================================================

def build_movement_resp(entity_id: int, cur_x: int, cur_y: int,
                        dst_x: int, dst_y: int, speed: int = 100) -> bytes:
    """Build the 30B S->C movement response framed payload.

    Two framed sub-messages packed together:
      Sub 1 (2B):  [LE16 0x006D]                               — MOVE_RESP flag
      Sub 2 (24B): [LE16 0x0005][LE32 entity_id]               — ENTITY_ANIM
                   [LE32 cur_x][LE32 cur_y][LE32 dst_x][LE32 dst_y][LE16 speed]

    Returns the 30B framed payload (no outer packet header).
    Pass directly to PacketBuilder.build_packet().
    """
    sub1 = struct.pack('<H', 0x006D)
    sub2 = struct.pack('<HIIIIIH', 0x0005, entity_id,
                       cur_x, cur_y, dst_x, dst_y, speed)
    return (struct.pack('<H', len(sub1)) + sub1 +
            struct.pack('<H', len(sub2)) + sub2)


def build_keepalive_tick() -> bytes:
    """Build the 10B 0x018A keepalive tick (type=4, sent every ~1s).

    Format: [LE16 opcode][LE32 type=4][LE32 data=0]
    """
    return struct.pack('<HII', 0x018A, 4, 0)


def build_keepalive_timer(minute: int) -> bytes:
    """Build the 14B 0x018A keepalive timer (type=8, sent every ~60s).

    Format: [LE16 opcode][LE32 type=8][LE32 minute][LE32 zeros]
    minute: incrementing minute counter since session start.
    """
    return struct.pack('<HIII', 0x018A, 8, minute, 0)


def build_ack_response(entity_id: int, area_id: int = 0x0020A0C3) -> bytes:
    """Build the S->C 64B ACK response (6 sub-messages).

    Sent in response to C->S 0x0003 ACK. Contains:
      0x0142 (3B)  × 2 — toggle flags (0x00 each)
      0x001D (16B) × 1 — entity_id + constant fields
      0x0027 (10B) × 1 — area reference
      0x0040 (10B) × 1 — area reference
      0x003F (10B) × 1 — area reference

    The entity_id varies per session; area_id is zone-specific.
    Confirmed from two separate pcap streams.
    """
    eid_lo = entity_id & 0xFFFF
    eid_hi = (entity_id >> 16) & 0xFFFF

    entity_setting = struct.pack('<HHHHHHHH',
                                 0x001D, eid_lo, eid_hi,
                                 0x3501, 0x074E, 0, 0x21, 0)

    return (
        pack_sub(build_toggle_0142(0)) +
        pack_sub(build_toggle_0142(0)) +
        pack_sub(entity_setting) +
        pack_sub(build_area_ref(0x0027, area_id)) +
        pack_sub(build_area_ref(0x0040, area_id)) +
        pack_sub(build_area_ref(0x003F, area_id))
    )  # 5+5+18+12+12+12 = 64B


def build_redirect(world_host: str, world_port: int,
                   session_token: bytes = None) -> bytes:
    """Build the 34B S->C redirect payload (0x0020).

    Sent by login server to direct the client to the game world server.
    Format: [LE16 sub_len=32][sub_data(32)]
    Sub-data layout (32 bytes):
      [0:2]   LE16 type = 0x0004
      [2:4]   LE16 padding = 0
      [4:8]   4B session_token (random)
      [8]     B flag = 0x01
      [9:25]  16B IP string (null-terminated, zero-padded)
      [25:27] LE16 port
      [27:32] 5B zeros

    Confirmed from pcap byte analysis. Matches game_server._build_redirect().
    """
    if session_token is None:
        session_token = os.urandom(4)

    ip_encoded = world_host.encode('ascii') + b'\x00'
    ip_field = (ip_encoded + b'\x00' * 16)[:16]

    sub = bytearray(32)
    struct.pack_into('<H', sub, 0, 0x0004)
    struct.pack_into('<H', sub, 2, 0x0000)
    sub[4:8] = session_token[:4]
    sub[8] = 0x01
    sub[9:25] = ip_field
    struct.pack_into('<H', sub, 25, world_port)
    # sub[27:32] remains zeros

    return struct.pack('<H', 32) + bytes(sub)


def build_server_timer(minutes: int, flag: int = 1) -> bytes:
    """Build a 7B 0x0015 SERVER_TIMER sub-message.

    Broadcast every 3 minutes. Contains an elapsed-minutes counter.
    Format: [LE16 opcode][B flag][LE32 minutes]
    Confirmed from relay capture: flag=1, minutes=15 at ~15 min into session.
    """
    return struct.pack('<HBI', 0x0015, flag, minutes)


def build_inspect_player_resp(name: str, level: int,
                               entity_id: int = 0, flag: int = 1) -> bytes:
    """Build a 19B 0x003A INSPECT_PLAYER_RESP sub-message.

    Sent in response to C->S 0x002C INSPECT_PLAYER.
    Format: [LE16 opcode][10B name null-padded][LE16 level][LE32 entity_id][B flag]
    Confirmed from relay captures: "gilber3\\0\\0\\0" + level=12 + unknown + 0x01.
    """
    return (struct.pack('<H', 0x003A) +
            pad_name(name, 10) +
            struct.pack('<HIB', level, entity_id, flag))


def build_equip_result(result: int = 0, slot: int = 0) -> bytes:
    """Build an 8B 0x0063 EQUIP_RESULT sub-message.

    Sent in response to C->S 0x0049 EQUIP_ITEM2 within ~40ms.
    Format: [LE16 opcode][LE32 result][LE16 slot]
    Confirmed from relay captures: all-zero payload = success.
    result: 0 = success, non-zero = error code.
    """
    return struct.pack('<HIH', 0x0063, result, slot)


def build_entity_despawn(entity_id: int, area_id: int = 0x0020A0C3,
                          flags: int = 0x0102) -> bytes:
    """Build a 14B 0x001B ENTITY_DESPAWN sub-message (simple variant).

    Sent when an entity leaves the player's view or is removed from the world.
    Format: [LE16 opcode][LE32 entity_id][LE16 flags][LE32 area_id][LE16 zeros]
    Confirmed from relay captures (14B simple variant).
    A larger 92B variant exists for complex despawns but its format is unknown.
    """
    return struct.pack('<HIHIH', 0x001B, entity_id, flags, area_id, 0)


def build_entity_status(entity_id: int, status_a: int = 0,
                        status_b: int = 0) -> bytes:
    """Build a 13B 0x000B ENTITY_STATUS sub-message.

    Sent to update an entity's status flags (HP bar visibility, state, etc.).
    Format (confirmed from pcap, 13 bytes fixed):
      [LE16 opcode=0x000B]
      [LE32 entity_id]
      [B status_a]   — primary status (0x01=alive, 0x07=damaged, 0x00=dead?)
      [B status_b]   — secondary status (0x01, 0x14 observed)
      [5B zeros]
    """
    return struct.pack('<HIBB', 0x000B, entity_id, status_a, status_b) + \
           b'\x00' * 5


def build_entity_action(entity_id: int, action_type: int,
                        target_id: int = 0, data: int = 0) -> bytes:
    """Build a variable 0x000D ENTITY_ACTION sub-message (S->C).

    Sent when an entity performs an action (attack, interact, pickup).
    Format (from pcap analysis, ~15 bytes minimum):
      [LE16 opcode=0x000D]
      [LE32 entity_id]     — entity performing the action
      [LE16 action_type]   — action ID (attack=1, loot=2, interact=3?)
      [LE32 target_id]     — target entity (0 if no target)
      [LE16 data]          — extra data (damage? item_id?)
    """
    return struct.pack('<HIHIH', 0x000D, entity_id, action_type,
                       target_id, data)


def build_pet_status_tick(pet_entity_id: int, status: int = 1,
                          hp: int = 0) -> bytes:
    """Build an 10B 0x0013 PET_STATUS_TICK sub-message.

    Periodic pet/robot status heartbeat, appears in keepalive batches.
    Format:
      [LE16 opcode=0x0013]
      [LE32 pet_entity_id]
      [LE16 status]    — 1=active, 0=idle?
      [LE16 hp]        — pet HP or stamina
    """
    return struct.pack('<HIHH', 0x0013, pet_entity_id, status, hp)


def build_entity_move(entity_id: int, cur_x: int, cur_y: int,
                      dst_x: int, dst_y: int, speed: int = 50) -> bytes:
    """Build a 24B 0x0005 ENTITY_MOVE sub-message.

    Sent when another entity (NPC, mob, player) moves. Parsed by client
    sub_5EC7B0 (opcode 0x0005 handler in the world dispatch table).

    Format (24 bytes total):
      [LE16 opcode=0x0005]
      [LE32 entity_id]
      [LE32 cur_x]
      [LE32 cur_y]
      [LE32 dst_x]
      [LE32 dst_y]
      [LE16 speed]

    NB: earlier version used 0x0018 but that's the emote/animation
    handler (sub_5F14D0). The real entity-move opcode is 0x0005.
    """
    return struct.pack('<HIIIIIH', 0x0005, entity_id,
                       cur_x, cur_y, dst_x, dst_y, speed)


def build_combat_action(source_id: int, target_id: int,
                        skill_id: int = 0, damage: int = 0,
                        action_type: int = 1, flags: int = 0) -> bytes:
    """Build a 0x0019 COMBAT_ACTION sub-message (S->C).

    Sent when combat happens (hit, miss, skill effect).
    Format (from pcap analysis, 27-53B range, using 27B base):
      [LE16 opcode=0x0019]
      [LE32 source_entity]  — attacker
      [LE32 target_entity]  — defender
      [LE16 action_type]    — 1=normal attack, 2=skill, 3=miss?
      [LE16 skill_id]       — skill used (0 for basic attack)
      [LE32 damage]         — damage dealt
      [LE16 flags]          — hit flags (crit=0x01, miss=0x02, etc.)
      [5B zeros]            — trailing data
    """
    return struct.pack('<HIIHHI', 0x0019, source_id, target_id,
                       action_type, skill_id, damage) + \
           struct.pack('<H', flags) + b'\x00' * 5


def build_player_title(entity_id: int, title: str) -> bytes:
    """Build a 0x001F PLAYER_TITLE sub-message.

    Broadcast when a player's name/title changes.
    Format:
      [LE16 opcode=0x001F]
      [LE32 entity_id]
      [2B prefix "PT"]
      [var name/title null-terminated]
    """
    title_bytes = title.encode('utf-8')[:24] + b'\x00'
    return struct.pack('<HI', 0x001F, entity_id) + b'PT' + title_bytes


def build_chat_msg(sender_entity_id: int, sender_name: str,
                   message: str, pos_x: int = 0, pos_y: int = 0,
                   chat_type: int = 0x0001, channel: int = 0x01) -> bytes:
    """Build a 0x001E CHAT_MSG sub-message (S->C runtime).

    Delivers a chat message to the client.
    Format (from world_server.py working implementation):
      [LE16 opcode=0x001E]
      [LE16 chat_type]     — 0x0001=system/NPC, 0x0017=player?
      [LE32 entity_id]     — sender entity
      [8B sender_name]     — null-padded
      [LE32 pos_x]
      [LE32 pos_y]
      [B channel]          — 0x01=system, 0x00=normal
      [var message]        — null-terminated
    """
    name8 = sender_name.encode('utf-8')[:7]
    name8 = name8 + b'\x00' * (8 - len(name8))
    msg_bytes = message.encode('utf-8')[:200] + b'\x00'
    return struct.pack('<HHI', 0x001E, chat_type, sender_entity_id) + \
           name8 + struct.pack('<IIB', pos_x, pos_y, channel) + msg_bytes


def build_player_appears(area_id: int, entity_id: int,
                         player_name: str, level: int, class_id: int,
                         guild_name: str = "",
                         spawn_flag: int = 1) -> bytes:
    """Build an 85B 0x0028 PLAYER_APPEARS sub-message.

    Broadcast when a player enters view range.
    Format (from pcap analysis of 4 captured instances, 85B fixed):
      [LE16 opcode=0x0028]
      [LE32 area_id]         — zone area (0x0020A0C3)
      [B spawn_flag]         — 1=new spawn, 0=update
      [10B player_name]      — null-terminated, zero-padded
      [2B unknown]           — varies
      [2B unknown]
      [4B short_tag]         — "AMe\\0" observed
      [4B zeros]
      [LE32 entity_id]
      [4B zeros/flags]
      [B flag=0x01]
      [2B unknown]
      [2B class_data]        — level/class encoding
      [10B appearance]       — equipment/appearance bytes
      [2B unknown]
      [2B zeros]
      [B buff_count]
      [12B buff_data]
      [2B zeros]
      [B guild_prefix_len]
      [B guild_prefix]
      [10B guild_name]       — null-terminated
      [2B unknown trailing]
      [2B constant=0x0001]
      [2B constant=0x256B]
    """
    data = bytearray(83)  # 85 - 2 for opcode

    # area_id at offset 0
    struct.pack_into('<I', data, 0, area_id)
    # spawn_flag at offset 4
    data[4] = spawn_flag
    # player_name at offset 5 (10 bytes)
    name_bytes = player_name.encode('utf-8')[:9] + b'\x00'
    name_bytes = name_bytes + b'\x00' * (10 - len(name_bytes))
    data[5:15] = name_bytes
    # Short tag "AMe\0" at offset 19
    data[19:23] = b'AMe\x00'
    # entity_id at offset 27
    struct.pack_into('<I', data, 27, entity_id)
    # flag at offset 35
    data[35] = 0x01
    # level encoding at offset 37
    data[37] = level & 0xFF
    # class_id at offset 38
    data[38] = class_id & 0xFF
    # guild name
    if guild_name:
        guild_bytes = guild_name.encode('utf-8')[:9] + b'\x00'
        data[67] = len(guild_name) + 1
        data[69:69 + len(guild_bytes)] = guild_bytes[:10]
    # trailing constants
    struct.pack_into('<HH', data, 79, 0x0001, 0x256B)

    return struct.pack('<H', 0x0028) + bytes(data)


def build_world_chat(sender_entity_id: int, sender_name: str,
                     message: str, channel: int = 0x00) -> bytes:
    """Build a 0x0128 chat sub-message (S->C).

    THIS IS THE REAL CHAT DISPLAY OPCODE. Confirmed from client decompile
    of sub_5F3B40 (slot for 0x0128 in the world dispatch table). The
    previously-guessed 0x001E turned out to be a session counter, not
    a chat render.

    Layout (confirmed from sub_5F3B40):
      [0-1]   LE16  opcode = 0x0128
      [2-5]   LE32  source entity_id
      [6]     u8    channel (see below)
      [7-23]  17B   sender name (NUL-terminated, NUL-padded)
      [24-]   var   message text (NUL-terminated)

    Known channel values and their render paths (empirically verified):
      0, 1        ✅ chat tab, no name prefix — general/system
      2, 10       ❌ dropped (needs a secondary name at +7? — unverified)
      11          ✅ chat tab WITH "name:" prefix + speech bubble over head
      12          ❌ dropped (filter-gated?)
      13          ✅ chat tab WITH "name:" prefix (world/guild variant)
      15          ✅ chat tab WITH "name:" prefix
      16          ✅ chat tab WITH "name:" prefix

    Channels 11/13/15/16 consume the first byte of the message field at
    offset 24 as a sub-category indicator — actual text starts at 25. We
    prepend a NUL so the displayed message isn't missing its first char.

    For server-side system messages, channel 0 is the simplest renderer.
    """
    _PREFIX_CHANNELS = {0x0B, 0x0D, 0x0F, 0x10}
    # Name field spans bytes 7..23 inclusive = 17 bytes. Message MUST
    # start at offset 24 or the handler reads garbage for the channel.
    name_bytes = sender_name.encode('utf-8')[:16] + b'\x00'
    name_field = (name_bytes + b'\x00' * 17)[:17]
    raw_msg = message.encode('utf-8')[:200]
    # Channels that eat the first byte as a sub-category need a filler.
    if channel in _PREFIX_CHANNELS:
        raw_msg = b'\x00' + raw_msg
    msg_bytes = raw_msg + b'\x00'

    return struct.pack('<HIB', 0x0128, sender_entity_id, channel) + \
           name_field + msg_bytes


def build_buff_info(entity_id: int, buffs: list[tuple[int, int, int]]
                    ) -> bytes:
    """Build a 0x006A BUFF_INFO sub-message.

    Sent to display active buff/debuff icons on an entity.
    Format (from pcap, 108-134B range):
      [LE16 opcode=0x006A]
      [LE32 entity_id]
      [LE16 buff_count]
      [N x buff_entry]:
        [LE16 buff_id]     — skill/buff ID
        [LE32 duration_ms] — remaining duration in ms
        [LE32 caster_id]   — entity that applied the buff

    buffs: list of (buff_id, duration_ms, caster_entity_id) tuples
    """
    header = struct.pack('<HIH', 0x006A, entity_id, len(buffs))
    entries = b''
    for buff_id, duration, caster_id in buffs:
        entries += struct.pack('<HII', buff_id, duration, caster_id)
    return header + entries


def build_loot_announce(entity_id: int, area_id: int = 0x0020A0C3,
                         loot_id: int = 0) -> bytes:
    """Build a 24B 0x0022 LOOT_ANNOUNCE sub-message.

    Sent when a mob drops loot. Appears alongside 0x000D+0x0012 cluster.
    Format: [LE16 opcode][LE32 entity_id][LE32 area_id][LE32 loot_id][10B zeros]
    Confirmed from relay captures: loot_id=0x191 (401) on "Rare Lizard's skin" drop.
    """
    return (struct.pack('<HIII', 0x0022, entity_id, area_id, loot_id) +
            b'\x00' * 10)


# =============================================================================
# Builder registry — maps opcode to (builder_func, expected_size)
# Used by the verification script to auto-rebuild sub-messages.
# =============================================================================

BUILDERS = {
    # Fully decoded — structured parameters
    0x0007: ('build_entity_pos', 7),
    0x0027: ('build_area_ref', 10),
    0x003F: ('build_area_ref', 10),
    0x0040: ('build_area_ref', 10),
    0x005B: ('build_slot_table', 218),
    0x005C: ('build_empty_005C', 39),
    0x005D: ('build_timestamp_005D', 6),
    0x012A: ('build_indexed_slot_012A', 23),
    0x0142: ('build_toggle_0142', 3),
    0x0144: ('build_empty_0144', 8),
    0x0145: ('build_empty_0145', 4),
    0x0162: ('build_empty_0162', 10),
    0x0164: ('build_empty_0164', 19),
    0x0178: ('build_pair_0178', 10),
    0x017D: ('build_empty_017D', 4),
    0x0185: ('build_entity_ref_0185', 14),
    0x018F: ('build_triple_018F', 14),

    # Partially decoded — structured + raw tail
    0x0042: ('build_char_stats', 107),
    0x014A: ('build_party_name', 19),

    # New opcodes from Gameplay.pcap capture
    0x018E: ('build_zone_list_captured', 166),
    0x0191: ('build_session_config', 10),

    # Decoded from captured constants
    0x0014: ('build_flag_0014', 9),
    0x001E: ('build_entity_001E', 6),
    0x0021: ('build_anchor_0021', 24),
    0x0149: ('build_currency_0149', 38),
    0x0158: ('build_skill_slot', 74),
    0x0160: ('build_settings_0160', 27),
    0x016F: ('build_flags_016F', 20),
    0x017A: ('build_data_017A', 19),
    0x018A: ('build_data_018A', 23),

    # Gameplay S->C — confirmed from relay captures
    0x001B: ('build_entity_despawn', 14),
    0x0015: ('build_server_timer', 7),
    0x0022: ('build_loot_announce', 24),
    0x003A: ('build_inspect_player_resp', 19),
    0x0063: ('build_equip_result', 8),

    # New S->C builders — from pcap analysis (2026-03-11)
    0x000B: ('build_entity_status', 13),
    0x000D: ('build_entity_action', None),  # variable size
    0x0013: ('build_pet_status_tick', 10),
    0x0018: ('build_entity_move', 26),
    0x0019: ('build_combat_action', None),  # variable 27-53B
    0x001F: ('build_player_title', None),   # variable
    0x0028: ('build_player_appears', 85),
    0x006A: ('build_buff_info', None),      # variable 108-134B
    0x0128: ('build_world_chat', None),     # variable
}

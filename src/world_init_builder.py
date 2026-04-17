"""Dynamic init packet builder for Angels Online.

Loads init packet templates from seed hex files, then patches per-player
data (entity_id, stats, position, timestamp) into them before sending.

This replaces world_init_data.py's static hex file loading with a
hybrid approach: templates for complex/unknown sub-messages, dynamic
builders for known sub-messages that vary per player.
"""

import os
import struct
import time
import logging
import sqlite3

log = logging.getLogger('world_init_builder')

_SEED_DIR = os.path.join(os.path.dirname(__file__), '..', 'tools', 'seed_data')

# Cached decompressed init packet templates
_pkt1_template: bytes | None = None
_pkt2_template: bytes | None = None
_skill_template: bytes | None = None


def _load_and_decompress(filename: str) -> bytes | None:
    """Load a hex file and LZO-decompress it."""
    filepath = os.path.join(_SEED_DIR, filename)
    if not os.path.exists(filepath):
        log.warning(f"Missing seed file: {filepath}")
        return None
    with open(filepath, 'r') as f:
        raw = bytes.fromhex(f.read().strip())
    try:
        from lzallright import LZOCompressor
        return LZOCompressor().decompress(raw)
    except Exception as e:
        log.error(f"Failed to decompress {filename}: {e}")
        return None


def _load_templates():
    """Load and cache decompressed init packet templates."""
    global _pkt1_template, _pkt2_template, _skill_template
    if _pkt1_template is None:
        _pkt1_template = _load_and_decompress('init_pkt1.hex')
        if _pkt1_template:
            log.info(f"Loaded init_pkt1 template: {len(_pkt1_template)} bytes")
    if _pkt2_template is None:
        _pkt2_template = _load_and_decompress('init_pkt2.hex')
        if _pkt2_template:
            log.info(f"Loaded init_pkt2 template: {len(_pkt2_template)} bytes")
    if _skill_template is None:
        _skill_template = _load_and_decompress('init_pkt4.hex')
        if _skill_template:
            log.info(f"Loaded skill template: {len(_skill_template)} bytes")


def _find_sub_message(data: bytes, target_opcode: int,
                       target_size: int = 0) -> int:
    """Find the offset of a sub-message with the given opcode in a payload.

    Returns the offset of the sub_len field, or -1 if not found.
    If target_size > 0, also match on sub-message size.
    """
    pos = 0
    while pos + 4 <= len(data):
        sub_len = struct.unpack_from('<H', data, pos)[0]
        if pos + 2 + sub_len > len(data):
            break
        opcode = struct.unpack_from('<H', data, pos + 2)[0]
        if opcode == target_opcode:
            if target_size == 0 or sub_len == target_size:
                return pos
        pos += 2 + sub_len
    return -1


def _scan_sub_message(data: bytes, target_opcode: int,
                       target_size: int = 0) -> int:
    """Like _find_sub_message but tolerates leading bytes that aren't a
    valid sub-message frame. Walks the whole buffer looking for a
    plausible sub_len followed by the target opcode.

    Used for buffers where the 0x0042 we want isn't the first framed
    sub-message (init_pkt2 has leading non-sub bytes we can't always
    parse cleanly)."""
    for pos in range(len(data) - 4):
        sub_len = struct.unpack_from('<H', data, pos)[0]
        if sub_len == 0 or sub_len > 500:
            continue
        if pos + 2 + sub_len > len(data):
            continue
        opcode = struct.unpack_from('<H', data, pos + 2)[0]
        if opcode != target_opcode:
            continue
        if target_size and sub_len != target_size:
            continue
        return pos
    return -1


def get_char_stats_body(hp: int, hp_max: int, mp: int, mp_max: int) -> bytes:
    """Return a 107-byte 0x0042 CHAR_STATS sub-message body with the given
    HP/MP values and the rest of the tail copied verbatim from init_pkt2's
    captured 0x0042 (Soualz's Priest L18 stats).

    Keeping the captured tail means client-side stat displays (R.Atk, Dfs,
    resistances, weight) won't zero out when we re-send to update HP. The
    price: the displayed R.Atk will show Soualz's value after a level-up,
    not the computed one. Server-side damage calc uses class_stats and
    remains correct; only the client-side HUD numbers lag.

    Callers should wrap the result with pack_sub() before handing to
    PacketBuilder.
    """
    _load_templates()
    if _pkt2_template is None:
        # Last-ditch fallback: zeroed tail. Will blank the stat HUD but
        # won't crash.
        return struct.pack('<HIIII', 0x0042, hp, hp_max, mp, mp_max) + b'\x00' * 89
    off = _scan_sub_message(_pkt2_template, 0x0042, 107)
    if off < 0:
        return struct.pack('<HIIII', 0x0042, hp, hp_max, mp, mp_max) + b'\x00' * 89
    body_off = off + 4
    tail = _pkt2_template[body_off + 16:body_off + 105]  # 89 bytes
    return struct.pack('<HIIII', 0x0042, hp, hp_max, mp, mp_max) + tail


def _patch_entity_ref(data: bytearray, entity_id: int):
    """Patch 0x0185 entity reference with player's entity_id."""
    off = _find_sub_message(data, 0x0185, 14)
    if off >= 0:
        struct.pack_into('<I', data, off + 4, entity_id)  # after [2B len][2B opcode]


def _patch_anchor(data: bytearray, map_id: int):
    """Patch 0x0021 anchor with player's map_id."""
    off = _find_sub_message(data, 0x0021, 24)
    if off >= 0:
        struct.pack_into('<I', data, off + 12, map_id)  # index(4) + area_id(4) + map_id


def _patch_timestamp(data: bytearray):
    """Patch 0x005D timestamp with current time."""
    off = _find_sub_message(data, 0x005D, 6)
    if off >= 0:
        struct.pack_into('<I', data, off + 4, int(time.time()))


def _patch_char_stats(data: bytearray, stats: dict, hp: int, mp: int):
    """Patch 0x0042 character stats sub-message in init_pkt2.

    `stats` is the dict returned by class_stats.compute_stats; `hp` and `mp`
    are the current values from the DB (clamped to max by the caller).

    Layout (105 bytes after [sub_len:2][opcode:2], verified empirically):
        0-3:    HP_max (LE32)
        4-7:    HP (LE32)
        8-11:   MP_max (LE32)
        12-15:  MP (LE32)
        16-17:  stamina_max (LE16)
        18-19:  stamina (LE16)
        20-23:  ?
        24-27:  R.Atk (LE32)
        28-31:  L.Atk (LE32)
        32-35:  ?
        36-39:  Dfs (LE32)
        40-43:  ?
        44-47:  Spl Atk (LE32)
        48-51:  ?
        52-55:  Spl Dfs (LE32)
        58-59:  Rigor (LE16)
        62-63:  Agility (LE16)
        64-65:  Critical (LE16)
        72-73:  Soul (LE16)
    """
    off = _find_sub_message(data, 0x0042, 107)
    if off < 0:
        return
    base = off + 4
    struct.pack_into('<I', data, base,      stats['hp_max'])
    struct.pack_into('<I', data, base + 4,  hp)
    struct.pack_into('<I', data, base + 8,  stats['mp_max'])
    struct.pack_into('<I', data, base + 12, mp)
    struct.pack_into('<I', data, base + 24, stats['ratk'])
    struct.pack_into('<I', data, base + 28, stats['latk'])
    struct.pack_into('<I', data, base + 36, stats['dfs'])
    struct.pack_into('<I', data, base + 44, stats['sp_atk'])
    struct.pack_into('<I', data, base + 52, stats['sp_dfs'])
    # Secondary stat block (Rigor/Agility/Critical/Soul) uses LE16 pairs
    # (current, max). Set both equal so the UI doesn't show a debuff.
    struct.pack_into('<H', data, base + 56, stats['rigor'])
    struct.pack_into('<H', data, base + 58, stats['rigor'])
    struct.pack_into('<H', data, base + 60, stats['agility'])
    struct.pack_into('<H', data, base + 62, stats['agility'])
    struct.pack_into('<H', data, base + 64, stats['critical'])
    struct.pack_into('<H', data, base + 66, stats['critical'])
    struct.pack_into('<H', data, base + 72, stats['soul'])
    struct.pack_into('<H', data, base + 74, stats['soul'])


def _patch_currency(data: bytearray, gold: int):
    """Patch 0x0149 currency with player's gold."""
    off = _find_sub_message(data, 0x0149, 38)
    if off >= 0:
        struct.pack_into('<I', data, off + 12, gold)  # after [2B len][2B opcode][4B type][4B zero]


def _read_captured_entity_id(data: bytes) -> int | None:
    """Read the captured player entity_id from the 0x0002 profile sub-message.

    Returns the LE32 at profile offset 0, or None if the profile isn't found.
    """
    off = _find_sub_message(data, 0x0002, 4120)
    if off < 0:
        return None
    return struct.unpack_from('<I', data, off + 4)[0]


def _replace_entity_id_global(data: bytearray, old_id: int, new_id: int) -> int:
    """Replace every LE32 occurrence of old_id with new_id in-place.

    The capture hardcodes the player's entity_id in ~90 places
    (profile ref, position updates, entity spawn, equipment owner, etc.).
    A fresh character needs ALL of them rewritten or the client will
    render the captured character and ignore movement responses addressed
    to the new entity.

    Returns the number of replacements.
    """
    if old_id == new_id:
        return 0
    old_bytes = struct.pack('<I', old_id)
    new_bytes = struct.pack('<I', new_id)
    count = 0
    pos = 0
    while True:
        idx = data.find(old_bytes, pos)
        if idx < 0:
            break
        data[idx:idx + 4] = new_bytes
        count += 1
        pos = idx + 4
    return count


def _patch_player_profile(data: bytearray, player):
    """Patch the 0x0002 character profile sub-message in decompressed init_pkt1.

    Field offsets within the sub-message data (after [sub_len:2][opcode:2]),
    authoritative from IDA decompile of client sub_5E9C90 (the opcode 0x0002
    handler at slot 2 of the world-server dispatch table set up in
    sub_5E5350):

        0-3      LE32    entity_id
        4-7      LE32    ? → model+480
        8-11     LE32    X position
        12-15    LE32    Y position
        16-31    16B     character name (null-padded)
        33+      string  guild name (null-terminated, ~15B)
        50       BYTE    → sub_4B1FF0(v << 21) — facing direction?
        51-54    LE32    ? → model+572
        55-59    5B      appearance bytes 1-5 → model+576..+580
        60       BYTE    faction → model+744       ✅ (capture=3=Steel matches Soualz)
        62-63    WORD    → model+752
        64       BYTE    → model+1206              ❓ unknown semantic
        65-68    LE32    level → model+588
        69-92    6xLE32  stat block 1 → model+1208..+1228  ❓ capture holds 50087/49923/61582 —
                         looks like XP/timers/counters, NOT attack/defense
        93       BYTE    class_id → model+1232    ✅ (capture=1=Priest matches Soualz's job)
        102-105  LE32    HP_max → model+640
        106-109  LE32    HP → model+660
        110-113  LE32    MP_max → model+644
        114-117  LE32    MP → model+664
        118-119  WORD    stamina → model+648
        120-121  WORD    stamina_max → model+668
        126-157  5xLE32  combat stats → model+1248.. block  ✅ empirically verified against
                         Soualz capture (R.Atk=4553, L.Atk=4536, Dfs=4540,
                         Spl.Atk=4065, Spl.Dfs=4036) at offsets 126,130,138,146,154

    Known unknown: the "Job" text in the client still reads "Priest" even
    after patching data[base+93]. Either Job is derived from a secondary
    source (another sub-message, maybe 0x0042 char stats), or model+1232
    is a display variant and the actual job byte is elsewhere.
    """
    from class_stats import compute_stats, class_name

    off = _find_sub_message(data, 0x0002, 4120)
    if off < 0:
        log.warning("Profile sub-message 0x0002 not found in init_pkt1")
        return
    base = off + 4  # skip [sub_len:2][opcode:2]

    keys = player.keys()
    name = player['name']
    class_id = player['class_id'] if 'class_id' in keys else 0
    level = player['level'] if 'level' in keys else 1
    app = tuple(
        (player[f'app{i}'] & 0xFF) if f'app{i}' in keys else 0
        for i in range(5)
    )

    # Compute stats from class + level. HP/MP in the DB track *current*
    # values (reduced by damage); HP_max/MP_max come from the class table
    # so they reflect the current level even if DB defaults are stale.
    stats = compute_stats(class_id, level)
    hp_max = stats['hp_max']
    mp_max = stats['mp_max']
    hp = player['hp'] if 'hp' in keys else hp_max
    mp = player['mp'] if 'mp' in keys else mp_max
    # Clamp current HP/MP to new max in case class/level changed
    hp = min(hp, hp_max)
    mp = min(mp, mp_max)

    # Name (16 bytes null-padded)
    name_bytes = name.encode('ascii', errors='replace')[:16]
    data[base + 16:base + 16 + 16] = (
        name_bytes + b'\x00' * (16 - len(name_bytes)))

    # Appearance bytes — drive the 3D model. Must NOT be clobbered by
    # class_id (they live at a different offset).
    for i in range(5):
        data[base + 55 + i] = app[i]

    # data[60] is faction (model+744). Capture=3=Steel; leave it untouched
    # until we track faction in the DB.
    # data[93] is class_id (model+1232). Capture=1=Priest for Soualz confirmed
    # against the decompile of sub_5E9C90 at a2+95. Known issue: the client's
    # "Job" label still shows Priest after we patch this — the label likely
    # reads from a secondary field we haven't located yet.
    data[base + 93] = class_id & 0xFF

    # Level (LE32 at 65-68)
    struct.pack_into('<I', data, base + 65, level)

    # Combat stats (LE32 each) — offsets verified by decompressing the
    # captured init_pkt1 and matching Soualz's known Priest values
    # (R.Atk=4553, L.Atk=4536, Dfs=4540, Spl Atk=4065, Spl Dfs=4036).
    struct.pack_into('<I', data, base + 126, stats['ratk'])
    struct.pack_into('<I', data, base + 130, stats['latk'])
    struct.pack_into('<I', data, base + 138, stats['dfs'])
    struct.pack_into('<I', data, base + 146, stats['sp_atk'])
    struct.pack_into('<I', data, base + 154, stats['sp_dfs'])

    # HP/MP (LE32) — confirmed against sub_5E9C90
    struct.pack_into('<I', data, base + 102, hp_max)
    struct.pack_into('<I', data, base + 106, hp)
    struct.pack_into('<I', data, base + 110, mp_max)
    struct.pack_into('<I', data, base + 114, mp)

    log.info(
        f"Patched 0x0002 profile: name='{name}' class={class_id}"
        f"({class_name(class_id)}) lvl={level} hp={hp}/{hp_max} "
        f"mp={mp}/{mp_max} atk={stats['ratk']} dfs={stats['dfs']}")


def build_init_packets_for_player(player: sqlite3.Row) -> list[tuple[bytes, bool]]:
    """Build init packets with per-player data patched in.

    Takes a player DB row and returns init packets in the same format
    as the old get_init_packets(): list of (payload_bytes, compressed_flag).

    The payload_bytes are LZO-compressed, matching what the client expects.
    """
    _load_templates()

    from lzallright import LZOCompressor
    lzo = LZOCompressor()

    entity_id = player['entity_id']
    # Init template was captured from map 101. Using any other map_id
    # without rebuilding all 137 sub-messages causes client crash because
    # entity/area data doesn't match the map. Default to 101 until we
    # support per-map init packet generation.
    # Template was captured from map 129. Don't patch to a different map
    # unless all entity/area data is also rebuilt for the target map.
    map_id = None  # use template's original map_id (129)

    # Derive stats from class_id + level via the class table. Current HP/MP
    # in the DB may be stale (old hardcoded defaults) — clamp to new max.
    from class_stats import compute_stats
    keys = player.keys()
    class_id = player['class_id'] if 'class_id' in keys else 0
    level = player['level'] if 'level' in keys else 1
    stats = compute_stats(class_id, level)
    hp = min(player['hp'] if 'hp' in keys else stats['hp_max'], stats['hp_max'])
    mp = min(player['mp'] if 'mp' in keys else stats['mp_max'], stats['mp_max'])
    gold = player['gold'] if 'gold' in keys else 500

    packets = []

    # Try raw pre-compressed files first (exact capture bytes, no
    # decompress/recompress cycle that could alter LZO output)
    raw1_path = os.path.join(_SEED_DIR, 'init_pkt1_raw.hex')
    raw2_path = os.path.join(_SEED_DIR, 'init_pkt2_raw.hex')

    if os.path.exists(raw1_path):
        with open(raw1_path, 'r') as f:
            raw1 = bytes.fromhex(f.read().strip())
        # Decompress, rewrite captured entity_id, patch profile, recompress
        decompressed = bytearray(lzo.decompress(raw1))
        captured_eid = _read_captured_entity_id(decompressed)
        if captured_eid is not None:
            n = _replace_entity_id_global(decompressed, captured_eid, entity_id)
            log.info(f"Rewrote captured entity 0x{captured_eid:08X} → "
                     f"0x{entity_id:08X} in init_pkt1 ({n} occurrences)")
        _patch_player_profile(decompressed, player)
        recompressed = lzo.compress(bytes(decompressed))
        packets.append((recompressed, True))
        log.info(f"Sending patched init_pkt1 ({len(recompressed)} bytes, "
                 f"player='{player['name']}', class={player['class_id']}, "
                 f"level={player['level']})")
    elif _pkt1_template:
        pkt1 = bytearray(_pkt1_template)
        _patch_entity_ref(pkt1, entity_id)
        if map_id is not None:
            _patch_anchor(pkt1, map_id)
        _patch_timestamp(pkt1)
        packets.append((lzo.compress(bytes(pkt1)), True))
        log.info(f"Built init_pkt1 for entity 0x{entity_id:08X} "
                 f"({len(pkt1)} bytes, compressed)")
    else:
        packets.append((b'', True))
        log.warning("No init_pkt1 template available")

    if os.path.exists(raw2_path):
        with open(raw2_path, 'r') as f:
            raw2 = bytes.fromhex(f.read().strip())
        # Decompress, rewrite captured entity_id, patch stats, recompress.
        # Need to re-derive captured_eid from pkt2 if pkt1 wasn't used.
        dec2 = bytearray(lzo.decompress(raw2))
        if 'captured_eid' in locals() and captured_eid is not None:
            old_eid = captured_eid
        else:
            old_eid = 0x3543018D  # known capture entity_id for Sarah's row
        n2 = _replace_entity_id_global(dec2, old_eid, entity_id)
        if n2:
            log.info(f"Rewrote captured entity 0x{old_eid:08X} → "
                     f"0x{entity_id:08X} in init_pkt2 ({n2} occurrences)")
        _patch_char_stats(dec2, stats, hp, mp)
        _patch_currency(dec2, gold)
        rec2 = lzo.compress(bytes(dec2))
        packets.append((rec2, True))
        log.info(f"Sending patched init_pkt2 ({len(rec2)} bytes, "
                 f"hp={hp}/{stats['hp_max']}, mp={mp}/{stats['mp_max']}, "
                 f"gold={gold})")
    elif _pkt2_template:
        pkt2 = bytearray(_pkt2_template)
        _patch_char_stats(pkt2, stats, hp, mp)
        _patch_currency(pkt2, gold)
        packets.append((lzo.compress(bytes(pkt2)), True))
        log.info(f"Built init_pkt2 for entity 0x{entity_id:08X} "
                 f"(hp={hp}/{stats['hp_max']}, mp={mp}/{stats['mp_max']}, "
                 f"gold={gold})")
    else:
        packets.append((b'', True))
        log.warning("No init_pkt2 template available")

    return packets


def build_skill_data() -> tuple[bytes, bool] | None:
    """Build the skill data packet (0x0158 x34).

    Currently returns the template verbatim. Future: build from player skill DB.
    """
    _load_templates()
    if _skill_template:
        from lzallright import LZOCompressor
        return (LZOCompressor().compress(_skill_template), True)
    return None

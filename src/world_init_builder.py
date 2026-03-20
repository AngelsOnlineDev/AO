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


def _patch_char_stats(data: bytearray, hp: int, hp_max: int,
                       mp: int, mp_max: int):
    """Patch 0x0042 character stats."""
    off = _find_sub_message(data, 0x0042, 107)
    if off >= 0:
        base = off + 4  # after [2B len][2B opcode]
        struct.pack_into('<I', data, base, hp)
        struct.pack_into('<I', data, base + 4, hp_max)
        struct.pack_into('<I', data, base + 8, mp)
        struct.pack_into('<I', data, base + 12, mp_max)


def _patch_currency(data: bytearray, gold: int):
    """Patch 0x0149 currency with player's gold."""
    off = _find_sub_message(data, 0x0149, 38)
    if off >= 0:
        struct.pack_into('<I', data, off + 12, gold)  # after [2B len][2B opcode][4B type][4B zero]


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
    map_id = player['map_id'] or 2  # default to Eden

    # Get player stats with fallback defaults
    hp = player['hp'] if 'hp' in player.keys() else 294
    hp_max = player['hp_max'] if 'hp_max' in player.keys() else 294
    mp = player['mp'] if 'mp' in player.keys() else 280
    mp_max = player['mp_max'] if 'mp_max' in player.keys() else 280
    gold = player['gold'] if 'gold' in player.keys() else 500

    packets = []

    # --- Packet 1: Patch and compress ---
    if _pkt1_template:
        pkt1 = bytearray(_pkt1_template)
        _patch_entity_ref(pkt1, entity_id)
        _patch_anchor(pkt1, map_id)
        _patch_timestamp(pkt1)
        packets.append((lzo.compress(bytes(pkt1)), True))
        log.info(f"Built init_pkt1 for entity 0x{entity_id:08X} "
                 f"({len(pkt1)} bytes, compressed)")
    else:
        packets.append((b'', True))
        log.warning("No init_pkt1 template available")

    # --- Packet 2: Patch and compress ---
    if _pkt2_template:
        pkt2 = bytearray(_pkt2_template)
        _patch_char_stats(pkt2, hp, hp_max, mp, mp_max)
        _patch_currency(pkt2, gold)
        packets.append((lzo.compress(bytes(pkt2)), True))
        log.info(f"Built init_pkt2 for entity 0x{entity_id:08X} "
                 f"(hp={hp}/{hp_max}, mp={mp}/{mp_max}, gold={gold})")
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

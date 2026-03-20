"""Area entity/NPC packet builder for the starting zone.

Two modes of operation:

1. Map-based (preferred): Reads NPC placements from the game's PAK map files
   and generates 0x0008 NPC spawn packets dynamically.  Uses npc.xml for names
   and sprite IDs.  Also generates 0x0007 position packets for each NPC.

2. Seed fallback: Loads the 17 area packets from hex files in tools/seed_data/.
   These are real-server captures that work regardless of whether PAK map data
   is available.

The map-based path is tried first; seed data is used as a fallback.

Runtime entity ID assignment
-----------------------------
The server assigns each spawned NPC a unique 32-bit runtime entity ID.  The
low-word (bits 0-15) is an incrementing counter; the high-word (bits 16-31) is
a fixed base derived from a constant.  This mirrors the pattern seen in the
real-server captures:

  Census Angel:   runtime_id = 0x13b00e85  (hi=0x13b0, lo=0x0e85)
  House Pickets:  runtime_id = 0x127f0d54  (hi=0x127f, lo=0x0d54)

For our server we use hi = 0x1234 and lo = incrementing from 0x1000.
"""

import os
import logging
from typing import Optional

log = logging.getLogger("area_entity_data")

_SEED_DIR = os.path.join(os.path.dirname(__file__), '..', 'tools', 'seed_data')

# (filename, compressed) — from area_meta.py
_AREA_PACKETS = [
    ("area_pkt01.hex", True),
    ("area_pkt02.hex", True),
    ("area_pkt03.hex", True),
    ("area_pkt04.hex", False),
    ("area_pkt05.hex", False),
    ("area_pkt06.hex", False),
    ("area_pkt07.hex", False),
    ("area_pkt08.hex", False),
    ("area_pkt09.hex", False),
    ("area_pkt10.hex", True),
    ("area_pkt11.hex", True),
    ("area_pkt12.hex", True),
    ("area_pkt13.hex", False),
    ("area_pkt14.hex", False),
    ("area_pkt15.hex", False),
    ("area_pkt16.hex", False),
    ("area_pkt17.hex", False),
]

_cached_seed_packets = None
_cached_seed_registry: Optional[dict] = None

# Runtime entity ID counter for map-based spawning
_next_entity_id: int = 0x12341000


def _alloc_entity_id() -> int:
    global _next_entity_id
    eid = _next_entity_id
    _next_entity_id = (_next_entity_id + 1) & 0xFFFFFFFF
    return eid


def build_seed_packets() -> list[tuple[bytes, bool]]:
    """Load all area packets from .hex files (seed/fallback mode).

    Returns:
        List of (payload_bytes, compressed_flag) tuples.
    """
    packets = []
    for filename, compressed in _AREA_PACKETS:
        filepath = os.path.join(_SEED_DIR, filename)
        if not os.path.exists(filepath):
            log.warning(f"Missing area data file: {filepath}")
            continue
        with open(filepath, 'r') as f:
            payload = bytes.fromhex(f.read().strip())
        packets.append((payload, compressed))
    log.info(f"Loaded {len(packets)} area packets from seed hex files")
    return packets


def _lzo_decompress(data: bytes) -> Optional[bytes]:
    """LZO-decompress data via lzallright. Returns None if unavailable/failed."""
    try:
        from lzallright import LZOCompressor
        return LZOCompressor().decompress(data)
    except ImportError:
        log.debug("lzallright not available; skipping compressed packet scan")
        return None
    except Exception as e:
        log.debug(f"LZO decompress failed: {e}")
        return None


def _scan_npc_spawns(payload: bytes, label: str, registry: dict) -> int:
    """Walk sub-messages in payload; register 0x0008 NPC spawn entries."""
    import struct as _struct
    NPC_SPAWN_OPCODE = 0x0008
    NPC_SPAWN_LEN = 65
    added = 0
    pos = 0
    while pos + 2 <= len(payload):
        sub_len = _struct.unpack_from('<H', payload, pos)[0]
        pos += 2
        if pos + sub_len > len(payload):
            break
        sub_data = payload[pos : pos + sub_len]
        pos += sub_len

        if sub_len != NPC_SPAWN_LEN:
            continue
        opcode = _struct.unpack_from('<H', sub_data, 0)[0]
        if opcode != NPC_SPAWN_OPCODE:
            continue

        eid_lo = _struct.unpack_from('<H', sub_data, 2)[0]
        eid_hi = _struct.unpack_from('<H', sub_data, 4)[0]
        runtime_id = (eid_hi << 16) | eid_lo
        npc_type_id = _struct.unpack_from('<H', sub_data, 47)[0]  # extra[16:18]

        if npc_type_id and runtime_id:
            registry[runtime_id] = npc_type_id
            name_raw = sub_data[18:31]
            null = name_raw.find(0)
            name = (name_raw[:null] if null >= 0 else name_raw).rstrip(b'\x00').decode('utf-8', errors='replace')
            log.debug(f"Seed registry [{label}]: 0x{runtime_id:08X} → NPC {npc_type_id} ({name!r})")
            added += 1
    return added


def build_seed_entity_registry() -> dict[int, int]:
    """Scan all seed hex files and extract runtime_id → npc_type_id.

    Scans three sources (all from the same real-server capture):
      1. Uncompressed area packets (area_pkt04–17): parsed directly.
      2. Compressed area packets (area_pkt01–03): LZO-decompressed first.
      3. Init packets (init_pkt1–2): LZO-decompressed first.

    The init packets contain NPC spawns for the starting area (e.g. Gaoler
    Angel, House Pickets, and others whose IDs may not appear in npc.xml).
    Pre-registering all ensures 0x000D clicks resolve to a type ID.

    Each payload is a sequence of sub-messages: [LE16 sub_len][sub_data].
    A 0x0008 NPC spawn sub-message is 65 bytes:
        [0:2]   opcode = 0x0008
        [2:4]   LE16 eid_lo  |  [4:6] LE16 eid_hi
        [10:14] LE32 tile_x  |  [14:18] LE32 tile_y
        [18:31] 13B name
        [31:65] 34B extra; extra[16:18] = LE16 npc_type_id

    Returns:
        Dict of runtime_entity_id → npc_type_id for all NPC spawns found.
    """
    registry: dict[int, int] = {}

    # --- Area packets (compressed and uncompressed) ---
    for filename, compressed in _AREA_PACKETS:
        filepath = os.path.join(_SEED_DIR, filename)
        if not os.path.exists(filepath):
            continue
        with open(filepath, 'r') as f:
            raw = bytes.fromhex(f.read().strip())
        payload = _lzo_decompress(raw) if compressed else raw
        if payload is not None:
            _scan_npc_spawns(payload, filename, registry)

    # --- Init packets (always LZO compressed) ---
    for init_file in ('init_pkt1.hex', 'init_pkt2.hex'):
        filepath = os.path.join(_SEED_DIR, init_file)
        if not os.path.exists(filepath):
            continue
        with open(filepath, 'r') as f:
            raw = bytes.fromhex(f.read().strip())
        payload = _lzo_decompress(raw)
        if payload is not None:
            _scan_npc_spawns(payload, init_file, registry)

    log.info(f"Seed entity registry: {len(registry)} NPCs from all seed hex files")
    return registry


def get_seed_entity_registry() -> dict[int, int]:
    """Return cached seed entity registry, building it on first call."""
    global _cached_seed_registry
    if _cached_seed_registry is None:
        _cached_seed_registry = build_seed_entity_registry()
    return _cached_seed_registry


def build_area_packets_from_map(map_data, npc_db: dict,
                                monster_db: Optional[dict] = None,
                                entity_registry: Optional[dict] = None,
                                ) -> list[tuple[bytes, bool]]:
    """Generate area entity packets from a loaded MapData.

    Builds one uncompressed packet per NPC and monster found in the map's
    entity records.  Both use 0x0008 NPC_SPAWN (confirmed from pcap — real
    server uses 0x0008 for monsters too, 0x000F is scenery).

    Args:
        map_data:        A MapData object (from map_loader.load_map*).
        npc_db:          Dict of npc_type_id -> {name, sprite_id} (from
                         map_loader.load_npc_xml()).
        monster_db:      Dict of monster_id -> {name, sprite_id, level, hp}
                         (from map_loader.load_monster_xml()).
        entity_registry: Optional dict to populate with
                         runtime_entity_id -> npc_type_id mappings.

    Returns:
        List of (payload_bytes, compressed=False) tuples.
        Returns empty list if no entities are found.
    """
    from packet_builders import build_npc_spawn, build_entity_pos, pack_sub

    # Only spawn NPCs from the map PAK.  The map's "monster" entities
    # (entity_id < 1500) are actually item/object definitions, not combat
    # mobs.  Real combat monsters (Slarm, Lily, etc.) come from the seed
    # area packets which are loaded as a fallback.
    npcs = map_data.npcs
    if not npcs:
        log.info("Map has no NPC entities; using seed data instead")
        return []

    packets = []
    for npc_ent in npcs:
        npc_type_id = npc_ent.entity_id
        npc_info = npc_db.get(npc_type_id, {})
        name = npc_info.get('name', f'NPC_{npc_type_id}')
        sprite_id = npc_info.get('sprite_id', 0)

        runtime_id = _alloc_entity_id()
        if entity_registry is not None:
            entity_registry[runtime_id] = npc_type_id

        tile_x = npc_ent.tile_x
        tile_y = npc_ent.tile_y
        eid_lo = runtime_id & 0xFFFF
        eid_hi = (runtime_id >> 16) & 0xFFFF

        npc_sub = build_npc_spawn(runtime_id, tile_x, tile_y,
                                  name, sprite_id, npc_type_id)
        pos_sub = build_entity_pos(eid_lo, eid_hi)

        payload = pack_sub(npc_sub) + pack_sub(pos_sub)
        packets.append((payload, False))

    # Append seed data packets (contains real monsters + scenery from captures)
    seed_pkts = build_seed_packets()
    packets.extend(seed_pkts)

    log.info(f"Generated {len(packets)} area packets from map {map_data.map_id} "
             f"({len(npcs)} map NPCs + {len(seed_pkts)} seed packets)")
    return packets


def get_area_packets(map_data=None, npc_db: Optional[dict] = None,
                     monster_db: Optional[dict] = None,
                     entity_registry: Optional[dict] = None,
                     ) -> list[tuple[bytes, bool]]:
    """Get area packets, preferring map-based generation over seed data.

    Args:
        map_data:        Optional MapData loaded from the game PAK.  If
                         provided and it contains entities, map-based packets
                         are generated.
        npc_db:          NPC database dict (from load_npc_xml()).  Required for
                         map-based generation.
        monster_db:      Monster database dict (from load_monster_xml()).
        entity_registry: Optional dict populated with
                         runtime_entity_id -> npc_type_id while spawning.

    Returns:
        List of (payload_bytes, compressed_flag) tuples ready to send.
    """
    if map_data is not None and npc_db is not None:
        pkts = build_area_packets_from_map(map_data, npc_db, monster_db, entity_registry)
        if pkts:
            return pkts
        log.info("Map-based spawning produced no packets; falling back to seed data")

    # Fall back to cached seed packets
    global _cached_seed_packets
    if _cached_seed_packets is None:
        _cached_seed_packets = build_seed_packets()
    return _cached_seed_packets


# Legacy entry point used by world_server.py before map loading was added
def build_area_packets() -> list[tuple[bytes, bool]]:
    """Load all area packets from .hex files (legacy, seed-only)."""
    return build_seed_packets()

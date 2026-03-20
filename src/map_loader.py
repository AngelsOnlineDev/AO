"""
Angels Online map loader.

Parses .MPC map files to extract:
  - Map dimensions and tile metadata
  - Entity placements (monsters and NPCs) from the binary entity section
  - Map event triggers (event XML) — including NPC interaction → dialog dispatch
  - Map-specific dialog trees (dialog XML)

MPC file structure:
  [4]   magic   "MAP\0"
  [4]   map_width  (tiles)
  [4]   map_height (tiles)
  [4]   flags
  [4]   tile_w (pixels, always 32)
  [4]   tile_h (pixels, always 32)
  [4]   entity_section_offset
  [4]   entity_section_size
  [4]   event_xml_offset
  [4]   event_xml_size
  [4]   ...
  [4]   entity_header_offset
  [4]   entity_header_size
  [4]   dialog_xml_offset

  Binary:
    [tile_w * tile_h * tile_record_size]  tile data
    [19 bytes]                            entity list header
    [N * 74 bytes]                        entity records
    [event_xml_size bytes]                event XML (<?xml ...>)
    [remaining]                           dialog XML (<?xml ...>)

  Entity record (74 bytes):
    [4]  x_pixel  → tile_x = x_pixel // 32
    [4]  y_pixel  → tile_y = y_pixel // 32
    [4]  entity_id  (matches monster.xml ID for monsters, npc.xml ID for NPCs ≥ 1500)
    [4]  flags
    [4]  direction  (0=none, 2=left/right facing)
    [54] padding/reserved

  Event XML uses action type 25 = start dialog:
    <觸發器 觸發="2">  → trigger_type 2 = player interaction (talk)
    <動作 編號="25"><參數 數值="DIALOG_ID"/></動作>
"""

import sys
import struct
import zlib
import logging
from pathlib import Path
from typing import Optional
import xml.etree.ElementTree as ET

# Allow importing pak_extractor from the tools directory at runtime
_TOOLS_DIR = Path(__file__).parent.parent / 'tools'
if str(_TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(_TOOLS_DIR))

log = logging.getLogger('map_loader')

ENTITY_RECORD_SIZE = 74
NPC_ID_MIN = 1500          # NPC IDs in npc.xml start at 1500
NPC_ID_MAX = 65535         # Exclude wrap-around garbage values (> uint16 max)
MAP_TRIGGER_TALK = 2       # player clicks/talks to an entity
MAP_ACTION_DIALOG = 25     # start a dialog tree
MAP_ACTION_WARP = 37       # zone warp (params: map_id, spawn_point, flag)

# Search order for PAK files containing map data.
# Newer update paks override older ones.
_PAK_SEARCH_ORDER = [
    'UPDATE10.PAK', 'UPDATE9.PAK', 'UPDATE8.PAK', 'UPDATE7.PAK',
    'UPDATE6.PAK', 'UPDATE5.PAK', 'UPDATE4.PAK', 'UPDATE3.PAK',
    'UPDATE2.PAK', 'update3.pak', 'update2.pak', 'update.pak', 'data1.pak',
]


class MapEntity:
    __slots__ = ('entity_id', 'tile_x', 'tile_y', 'direction', 'flags', 'is_npc')

    def __init__(self, entity_id: int, tile_x: int, tile_y: int, direction: int, flags: int):
        self.entity_id = entity_id
        self.tile_x = tile_x
        self.tile_y = tile_y
        self.direction = direction
        self.flags = flags
        self.is_npc = NPC_ID_MIN <= entity_id <= NPC_ID_MAX


class MapEvent:
    """A map event: when trigger fires, execute actions."""
    __slots__ = ('event_id', 'cursor', 'triggers')

    def __init__(self, event_id: int, cursor: int):
        self.event_id = event_id
        self.cursor = cursor
        self.triggers: list[dict] = []   # [{type, conditions, actions}]


class MapData:
    def __init__(self, map_id: int):
        self.map_id = map_id
        self.width = 0
        self.height = 0
        self.tile_w = 32
        self.tile_h = 32
        self.entities: list[MapEntity] = []
        self.events: dict[int, MapEvent] = {}          # event_id → MapEvent
        # entity_id → dialog_id (built from action-25 events)
        self.npc_dialogs: dict[int, int] = {}
        # dialog_id → dialog node dict (map-specific dialogs)
        self.local_dialogs: dict[int, dict] = {}
        # event_id → warp params (map_id, spawn_point, flag) from action-37 events
        self.warp_events: dict[int, tuple[int, int, int]] = {}

    @property
    def npcs(self) -> list[MapEntity]:
        return [e for e in self.entities if e.is_npc]

    @property
    def monsters(self) -> list[MapEntity]:
        return [e for e in self.entities if not e.is_npc]


def _parse_entity_section(data: bytes) -> list[MapEntity]:
    n_records = len(data) // ENTITY_RECORD_SIZE
    entities = []
    for i in range(n_records):
        off = i * ENTITY_RECORD_SIZE
        rec = data[off : off + ENTITY_RECORD_SIZE]
        x_pix = struct.unpack_from('<I', rec, 0)[0]
        y_pix = struct.unpack_from('<I', rec, 4)[0]
        eid   = struct.unpack_from('<I', rec, 8)[0]
        flags = struct.unpack_from('<I', rec, 12)[0]
        direc = struct.unpack_from('<I', rec, 16)[0]
        entity = MapEntity(eid, x_pix // 32, y_pix // 32, direc, flags)
        entities.append(entity)

        # Log the full 74-byte record for NPC entities so we can identify
        # where the event/dialog reference is stored in the padding bytes.
        if entity.is_npc:
            log.debug(f"NPC record type={eid} tile=({x_pix//32},{y_pix//32}) "
                      f"flags=0x{flags:08X} dir={direc} "
                      f"extra={rec[20:].hex(' ')}")

    return entities


def _parse_dialog_xml(xml_bytes: bytes) -> dict[int, dict]:
    """Parse the map-local dialog XML into a dict of dialog_id → node."""
    dialogs = {}
    try:
        root = ET.fromstring(xml_bytes.decode('utf-8', errors='replace'))
    except ET.ParseError:
        return dialogs

    for elem in root:
        if elem.tag != '對話':
            continue
        did = int(elem.attrib.get('編號', 0))
        msg_id = int(elem.attrib.get('訊息', 0))
        face = int(elem.attrib.get('臉譜', 0))
        cond_count = int(elem.attrib.get('條件', 0))

        node = {
            'id': did,
            'msg_id': msg_id,
            'face': face,
            'cond_count': cond_count,
            'options': [],
            'triggers': [],
        }
        for child in elem:
            if child.tag == '選項':
                opt_msg = int(child.attrib.get('訊息', 0))
                next_id = int(child.attrib.get('下一句', 0))
                node['options'].append({'msg_id': opt_msg, 'next_id': next_id})
            elif child.tag == '觸發器':
                trig = {
                    'type': int(child.attrib.get('觸發', 0)),
                    'conditions': [],
                    'next_id': 0,
                }
                for tchild in child:
                    if tchild.tag == '成立':
                        trig['next_id'] = int(tchild.attrib.get('下一句', 0))
                node['triggers'].append(trig)
        dialogs[did] = node
    return dialogs


def _parse_event_xml(xml_bytes: bytes) -> tuple[dict[int, MapEvent], dict[int, int], dict[int, tuple[int, int, int]]]:
    """
    Parse the map event XML.
    Returns (events_by_id, npc_dialog_map, warp_events).
    npc_dialog_map: entity_id → starting dialog_id (from action-type-25 events).
    warp_events: event_id → (map_id, spawn_point, flag) (from action-type-37 events).
    """
    events: dict[int, MapEvent] = {}
    npc_dialogs: dict[int, int] = {}
    warp_events: dict[int, tuple[int, int, int]] = {}

    try:
        root = ET.fromstring(xml_bytes.decode('utf-8', errors='replace'))
    except ET.ParseError:
        return events, npc_dialogs, warp_events

    for elem in root:
        if elem.tag != '事件':
            continue
        eid = int(elem.attrib.get('編號', 0))
        cursor = int(elem.attrib.get('游標', 0))
        event = MapEvent(eid, cursor)

        for trigger_elem in elem:
            if trigger_elem.tag != '觸發器':
                continue
            ttype = int(trigger_elem.attrib.get('觸發', -1))
            conditions = []
            actions = []

            for tchild in trigger_elem:
                if tchild.tag == '條件':
                    ctype = int(tchild.attrib.get('編號', 0))
                    params = [int(p.attrib.get('數值', 0)) for p in tchild if p.tag == '參數']
                    conditions.append({'type': ctype, 'params': params})
                elif tchild.tag == '動作':
                    atype = int(tchild.attrib.get('編號', 0))
                    params = [int(p.attrib.get('數值', 0)) for p in tchild if p.tag == '參數']
                    actions.append({'type': atype, 'params': params})

            event.triggers.append({
                'type': ttype,
                'conditions': conditions,
                'actions': actions,
            })

            # If this is a talk trigger (type 2) with a start-dialog action (type 25),
            # record the entity_id → dialog_id mapping
            if ttype == MAP_TRIGGER_TALK:
                for action in actions:
                    if action['type'] == MAP_ACTION_DIALOG and action['params']:
                        npc_dialogs[eid] = action['params'][0]

            # Capture warp actions (type 37) from any trigger type
            for action in actions:
                if action['type'] == MAP_ACTION_WARP and action['params']:
                    params = action['params']
                    map_id = params[0]
                    spawn_point = params[1] if len(params) > 1 else 0
                    flag = params[2] if len(params) > 2 else 1
                    warp_events[eid] = (map_id, spawn_point, flag)

        events[eid] = event

    return events, npc_dialogs, warp_events


def load_map(mpc_data: bytes, map_id: int) -> MapData:
    """
    Parse a decompressed .MPC file and return a MapData object.
    mpc_data: raw bytes of the decompressed map file.
    """
    md = MapData(map_id)

    if mpc_data[:4] != b'MAP\x00':
        raise ValueError(f"Invalid MPC magic: {mpc_data[:4]!r}")

    md.width  = struct.unpack_from('<I', mpc_data, 4)[0]
    md.height = struct.unpack_from('<I', mpc_data, 8)[0]
    md.tile_w = struct.unpack_from('<I', mpc_data, 16)[0] or 32
    md.tile_h = struct.unpack_from('<I', mpc_data, 20)[0] or 32

    entity_section_off  = struct.unpack_from('<I', mpc_data, 24)[0]
    entity_section_size = struct.unpack_from('<I', mpc_data, 28)[0]
    event_xml_off       = struct.unpack_from('<I', mpc_data, 32)[0]
    event_xml_size      = struct.unpack_from('<I', mpc_data, 36)[0]
    entity_hdr_off      = struct.unpack_from('<I', mpc_data, 52)[0]
    entity_hdr_size     = struct.unpack_from('<I', mpc_data, 56)[0]
    dialog_xml_off      = struct.unpack_from('<I', mpc_data, 60)[0]

    # Entity records start right after the 19-byte header
    ent_data_off = entity_hdr_off + entity_hdr_size
    ent_data_size = entity_section_size  # in bytes
    entity_bytes = mpc_data[ent_data_off : ent_data_off + ent_data_size]
    md.entities = _parse_entity_section(entity_bytes)

    # Event XML: starts 4 bytes into the section (skip padding before <?xml)
    event_xml_start = event_xml_off + 4
    event_xml_end_marker = mpc_data.find(b'</root>', event_xml_start)
    if event_xml_end_marker != -1:
        event_xml_bytes = mpc_data[event_xml_start : event_xml_end_marker + 7]
        # _parse_event_xml returns (events_by_id, event_id→dialog_id, warp_events)
        # We throw away the raw event_id keyed dict and rebuild it below
        # keyed by NPC type ID using entity.flags as the event ID link.
        event_id_dialogs: dict[int, int]
        md.events, event_id_dialogs, md.warp_events = _parse_event_xml(event_xml_bytes)
        log.debug(f"Map {map_id} events with talk-dialogs: "
                  f"{sorted(event_id_dialogs.items())}")

        # Second pass: match entity records to events via entity.flags.
        # The flags field in each binary entity record contains the event ID
        # that fires when a player interacts with (talks to) that entity.
        for entity in md.entities:
            if not entity.is_npc:
                continue
            if entity.flags == 0:
                log.debug(f"NPC type {entity.entity_id}: flags=0, no event link")
                continue
            if entity.flags in event_id_dialogs:
                md.npc_dialogs[entity.entity_id] = event_id_dialogs[entity.flags]
                log.debug(f"NPC type {entity.entity_id} flags=0x{entity.flags:X} "
                          f"→ dialog {event_id_dialogs[entity.flags]}")
            else:
                log.debug(f"NPC type {entity.entity_id} flags=0x{entity.flags:X} "
                          f"has no matching event (events: {sorted(md.events)[:10]})")

    # Dialog XML: starts 4 bytes into the section
    dialog_xml_start = dialog_xml_off + 4
    if dialog_xml_start < len(mpc_data):
        dialog_xml_bytes = mpc_data[dialog_xml_start:]
        # Clip at </root>
        end = dialog_xml_bytes.find(b'</root>')
        if end != -1:
            dialog_xml_bytes = dialog_xml_bytes[:end + 7]
        md.local_dialogs = _parse_dialog_xml(dialog_xml_bytes)

    log.info(f"Map {map_id}: {len(md.entities)} entities "
             f"({len(md.npcs)} NPCs, {len(md.monsters)} monsters), "
             f"{len(md.npc_dialogs)} NPC→dialog mappings, "
             f"{len(md.local_dialogs)} local dialogs")
    return md


def load_map_from_pak(pak_data: bytes, pak_entries: dict, map_id: int) -> Optional[MapData]:
    """
    Load a map from an already-parsed PAK file.
    pak_entries: dict from pak_extractor-style load_pak().
    """
    key = f"map/map{map_id:03d}.mpc"
    if key not in pak_entries:
        return None
    entry = pak_entries[key]
    raw = pak_data[entry['offset'] : entry['offset'] + entry['compressed_size']]
    mpc_data = zlib.decompress(raw, -15) if entry['method'] == 8 else raw
    return load_map(mpc_data, map_id)


def load_map_from_game_dir(game_dir: Path, map_id: int) -> Optional[MapData]:
    """Search game PAK files for map_id and load it.

    Checks PAK files in _PAK_SEARCH_ORDER so that newer update paks
    override older ones. Returns None if the map is not found.
    """
    try:
        from pak_extractor import (  # type: ignore[import]
            parse_eocd, read_central_directory, parse_central_directory)
    except ImportError:
        log.error("pak_extractor not found in tools/; cannot load maps from PAK")
        return None

    game_dir = Path(game_dir)
    key = f"map/map{map_id:03d}.mpc"

    for pak_name in _PAK_SEARCH_ORDER:
        pak_path = game_dir / pak_name
        if not pak_path.exists():
            continue
        try:
            pak_data = pak_path.read_bytes()
            eocd = parse_eocd(pak_data)
            cd_data = read_central_directory(pak_data, eocd)
            entries = parse_central_directory(cd_data)
            entries_dict = {e['name']: e for e in entries}
            if key not in entries_dict:
                continue
            log.debug(f"Loading {key} from {pak_name}")
            return load_map_from_pak(pak_data, entries_dict, map_id)
        except Exception as e:
            log.warning(f"Error reading {pak_name}: {e}")
            continue

    log.warning(f"Map {map_id} not found in any PAK file under {game_dir}")
    return None


def load_npc_xml(game_xml_dir: Path) -> dict[int, dict]:
    """Load npc.xml and return a dict of npc_type_id → {name, sprite_id}.

    Each entry contains:
      name      – display name (ASCII, from 名稱 attribute)
      sprite_id – sprite sheet ID (int, from 圖號 attribute)
    """
    path = Path(game_xml_dir) / 'npc.xml'
    result: dict[int, dict] = {}
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as e:
        log.error(f"Failed to load npc.xml from {path}: {e}")
        return result

    for elem in root:
        if elem.tag != 'npc':
            continue
        nid_str = elem.attrib.get('編號')
        if not nid_str:
            continue
        nid = int(nid_str)
        name = elem.attrib.get('名稱', '')
        sprite_str = elem.attrib.get('圖號', '0')
        try:
            sprite_id = int(sprite_str)
        except ValueError:
            sprite_id = 0
        result[nid] = {'name': name, 'sprite_id': sprite_id}

    log.info(f"Loaded {len(result)} NPC definitions from npc.xml")
    return result


def load_monster_xml(game_xml_dir: Path) -> dict[int, dict]:
    """Load monster.xml and return a dict of monster_id -> {name, sprite_id, level, hp}.

    monster.xml uses the same <npc> tag format as npc.xml but with IDs < 1500.
    """
    path = Path(game_xml_dir) / 'monster.xml'
    result: dict[int, dict] = {}
    try:
        root = ET.parse(str(path)).getroot()
    except (ET.ParseError, OSError) as e:
        log.error(f"Failed to load monster.xml from {path}: {e}")
        return result

    for elem in root:
        if elem.tag != 'npc':
            continue
        mid_str = elem.attrib.get('\u7de8\u865f')  # 編號
        if not mid_str:
            continue
        mid = int(mid_str)
        name = elem.attrib.get('\u540d\u7a31', '')  # 名稱
        sprite_str = elem.attrib.get('\u5716\u865f', '0')  # 圖號
        try:
            sprite_id = int(sprite_str)
        except ValueError:
            sprite_id = 0
        level_str = elem.attrib.get('\u7b49\u7d1a', '1')  # 等級
        try:
            level = int(level_str)
        except ValueError:
            level = 1
        hp_str = elem.attrib.get('HP', '100')
        try:
            hp = int(hp_str)
        except ValueError:
            hp = 100
        result[mid] = {'name': name, 'sprite_id': sprite_id, 'level': level, 'hp': hp}

    log.info(f"Loaded {len(result)} monster definitions from monster.xml")
    return result

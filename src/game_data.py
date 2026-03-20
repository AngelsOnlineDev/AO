"""Shared game-data singletons for Angels Online server.

Lazy-loaded caches for dialog trees, maps, NPC/monster databases,
and quests. Imported by handler modules instead of accessing
world_server globals.
"""

import logging
import config
from dialog_manager import DialogManager
from map_loader import MapData, load_map_from_game_dir, load_npc_xml, load_monster_xml
from quest_manager import QuestManager

log = logging.getLogger('game_data')

# Dialog engine
_dialog_manager: DialogManager | None = None

# Map cache
_map_cache: dict[int, MapData] = {}

# NPC database
_npc_db: dict[int, dict] | None = None

# Monster database
_monster_db: dict[int, dict] | None = None

# Quest engine
_quest_manager: QuestManager | None = None


def get_dialog_manager() -> DialogManager:
    global _dialog_manager
    if _dialog_manager is None:
        _dialog_manager = DialogManager()
        if config.GAME_XML_DIR.exists():
            _dialog_manager.load(config.GAME_XML_DIR)
        else:
            log.warning(f"GAME_XML_DIR not found: {config.GAME_XML_DIR}")
    return _dialog_manager


def get_quest_manager() -> QuestManager:
    global _quest_manager
    if _quest_manager is None:
        _quest_manager = QuestManager()
        quest_path = config.GAME_XML_DIR / 'quest.xml'
        if quest_path.exists():
            _quest_manager.load(quest_path)
        else:
            log.warning(f"quest.xml not found: {quest_path}")
    return _quest_manager


def get_map(map_id: int) -> MapData | None:
    """Load and cache a MapData for the given map_id."""
    if map_id in _map_cache:
        return _map_cache[map_id]
    if map_id == 0:
        return None
    md = load_map_from_game_dir(config.GAME_DIR, map_id)
    if md is None:
        log.warning(f"Map {map_id} not found in {config.GAME_DIR}")
    else:
        log.info(f"Map {map_id} loaded: {len(md.npcs)} NPCs, "
                 f"{len(md.monsters)} monsters, "
                 f"{len(md.npc_dialogs)} NPC→dialog mappings")
    _map_cache[map_id] = md
    return md


def get_npc_db() -> dict[int, dict]:
    """Load and cache the NPC database from npc.xml."""
    global _npc_db
    if _npc_db is None:
        if config.GAME_XML_DIR.exists():
            _npc_db = load_npc_xml(config.GAME_XML_DIR)
        else:
            log.warning(f"GAME_XML_DIR not found: {config.GAME_XML_DIR}")
            _npc_db = {}
    return _npc_db


def get_monster_db() -> dict[int, dict]:
    """Load and cache the monster database from monster.xml."""
    global _monster_db
    if _monster_db is None:
        if config.GAME_XML_DIR.exists():
            _monster_db = load_monster_xml(config.GAME_XML_DIR)
        else:
            log.warning(f"GAME_XML_DIR not found: {config.GAME_XML_DIR}")
            _monster_db = {}
    return _monster_db

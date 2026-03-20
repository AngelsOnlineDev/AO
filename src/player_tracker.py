"""Player tracking for zone-based broadcasting.

Maintains a spatial index of connected players by map_id,
enabling efficient zone-scoped broadcasting for movement,
chat, and player visibility.
"""

import logging

log = logging.getLogger('player_tracker')


class PlayerTracker:
    """Tracks connected players by zone for broadcasting."""

    def __init__(self):
        # map_id -> {entity_id -> session dict}
        self._by_map: dict[int, dict[int, dict]] = {}
        # entity_id -> session dict
        self._by_entity: dict[int, dict] = {}

    def register(self, entity_id: int, map_id: int, session: dict):
        """Register a player in a zone."""
        self._by_entity[entity_id] = session
        if map_id not in self._by_map:
            self._by_map[map_id] = {}
        self._by_map[map_id][entity_id] = session
        log.info(f"Registered entity 0x{entity_id:08X} on map {map_id} "
                 f"({len(self._by_map[map_id])} players on map)")

    def unregister(self, entity_id: int):
        """Remove a player from tracking."""
        session = self._by_entity.pop(entity_id, None)
        if session is None:
            return
        map_id = session.get('map_id', 0)
        if map_id in self._by_map:
            self._by_map[map_id].pop(entity_id, None)
            if not self._by_map[map_id]:
                del self._by_map[map_id]
        log.info(f"Unregistered entity 0x{entity_id:08X} from map {map_id}")

    def change_map(self, entity_id: int, new_map_id: int):
        """Move a player between zones."""
        session = self._by_entity.get(entity_id)
        if session is None:
            return
        old_map_id = session.get('map_id', 0)
        # Remove from old map
        if old_map_id in self._by_map:
            self._by_map[old_map_id].pop(entity_id, None)
            if not self._by_map[old_map_id]:
                del self._by_map[old_map_id]
        # Add to new map
        if new_map_id not in self._by_map:
            self._by_map[new_map_id] = {}
        self._by_map[new_map_id][entity_id] = session
        log.debug(f"Entity 0x{entity_id:08X}: map {old_map_id} -> {new_map_id}")

    def get_zone_sessions(self, map_id: int,
                           exclude_entity: int = 0) -> list[dict]:
        """Get all sessions in a zone, optionally excluding one entity."""
        zone = self._by_map.get(map_id, {})
        if exclude_entity:
            return [s for eid, s in zone.items() if eid != exclude_entity]
        return list(zone.values())

    def get_session(self, entity_id: int) -> dict | None:
        """Look up a session by entity_id."""
        return self._by_entity.get(entity_id)

    @property
    def player_count(self) -> int:
        """Total number of tracked players."""
        return len(self._by_entity)

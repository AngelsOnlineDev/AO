"""Multiplayer presence: broadcast player spawns, despawns, and movement
across sessions sharing a zone.

This module wraps PlayerTracker + the existing packet builders so handlers
don't need to know the packet formats or iterate sessions directly. It's
the layer where "one player moves" becomes "every other player on the map
sees the move".

Packets used:
    0x0028  PLAYER_APPEARS  — sent to observers when a player enters view
    0x001B  ENTITY_DESPAWN  — sent to observers when a player leaves
    0x0018  ENTITY_MOVE     — sent to observers when a player moves

The local player's OWN movement is sent back to them as 0x0005 inside the
30-byte movement_resp (handled by movement.py). This module only deals
with the "other players see you" direction.
"""

import logging
import asyncio

from packet_builders import (
    build_remote_player_spawn,
    build_remote_player_spawn_000E,
    build_entity_despawn,
    build_entity_move,
    pack_sub,
)

log = logging.getLogger('presence')


def _send_sub(session: dict, sub: bytes) -> None:
    """Wrap a sub-message in pack_sub, build a packet, write to the socket.

    Swallows disconnect errors so a dead session doesn't abort a broadcast.
    """
    try:
        pkt = session['builder'].build_packet(pack_sub(sub))
        session['writer'].write(pkt)
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass


def _spawn_subs(session: dict) -> list:
    """Build both remote-player spawn sub-messages for a session.

    Why two: 0x0001 (sub_5E97F0) works only for receivers still in
    char-select/login state — it carries the name/appearance but
    bails mid-game. 0x000E (sub_5EF410) works mid-game but has no
    name/appearance payload. Sending both lets each receiver process
    whichever handler its current state accepts; the unused one is
    ignored.
    """
    pixel_x = session.get('pos_x', 0)
    pixel_y = session.get('pos_y', 0)
    tile_x = pixel_x // 32
    tile_y = pixel_y // 32
    eid = session['entity_id']
    name = session.get('player_name', '?')
    player = session.get('player')
    def _p(key, default=0):
        if player is None:
            return default
        try:
            val = player[key]
        except (KeyError, IndexError):
            return default
        return default if val is None else val
    appearance = (
        _p('app0'), _p('app1'), _p('app2'), _p('app3'), _p('app4'),
    )
    class_id = _p('class_id')
    level = _p('level', 1)
    sub_0001 = build_remote_player_spawn(
        entity_id=eid,
        tile_x=tile_x,
        tile_y=tile_y,
        player_name=name,
        appearance=appearance,
        class_id=class_id,
        level=level,
    )
    sub_000E = build_remote_player_spawn_000E(
        entity_id=eid,
        tile_x=tile_x,
        tile_y=tile_y,
        sprite_id=999,
    )
    return [sub_0001, sub_000E]


async def send_existing_players_to(new_session: dict, tracker) -> None:
    """When a player joins a zone, send them a spawn for every player
    that was already in the zone so they immediately see everyone.
    """
    map_id = new_session.get('map_id', 0)
    my_eid = new_session['entity_id']
    other_sessions = [
        s for s in tracker.get_zone_sessions(map_id, exclude_entity=my_eid)
    ]
    if not other_sessions:
        return
    for other in other_sessions:
        for sub in _spawn_subs(other):
            _send_sub(new_session, sub)
    try:
        await new_session['writer'].drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass
    log.info(
        f"Sent {len(other_sessions)} existing player spawn(s) to "
        f"entity 0x{my_eid:08X}"
    )


async def broadcast_spawn(new_session: dict, tracker) -> None:
    """Announce that `new_session`'s player has entered the zone to
    every other player in the same zone.
    """
    map_id = new_session.get('map_id', 0)
    my_eid = new_session['entity_id']
    observers = tracker.get_zone_sessions(map_id, exclude_entity=my_eid)
    if not observers:
        return
    subs = _spawn_subs(new_session)
    for obs in observers:
        for sub in subs:
            _send_sub(obs, sub)
    # Drain all observer writers concurrently
    await asyncio.gather(
        *(_drain(obs) for obs in observers),
        return_exceptions=True,
    )
    log.info(
        f"Broadcast spawn of '{new_session.get('player_name', '?')}' "
        f"(0x{my_eid:08X}) to {len(observers)} observer(s) on map {map_id}"
    )


async def broadcast_despawn(session: dict, tracker) -> None:
    """Announce that `session`'s player has left the zone."""
    if session is None:
        return
    map_id = session.get('map_id', 0)
    my_eid = session.get('entity_id', 0)
    if not my_eid:
        return
    # Note: we exclude ourselves, but by the time this runs the session is
    # typically already unregistered from the tracker. Pass exclude anyway
    # in case the caller hasn't unregistered yet.
    observers = tracker.get_zone_sessions(map_id, exclude_entity=my_eid)
    if not observers:
        return
    sub = build_entity_despawn(my_eid)
    for obs in observers:
        _send_sub(obs, sub)
    await asyncio.gather(
        *(_drain(obs) for obs in observers),
        return_exceptions=True,
    )
    log.info(
        f"Broadcast despawn of 0x{my_eid:08X} to {len(observers)} "
        f"observer(s) on map {map_id}"
    )


async def broadcast_movement(session: dict, tracker,
                              cur_x: int, cur_y: int,
                              dst_x: int, dst_y: int,
                              speed: int) -> None:
    """Tell every other player in the zone that this player is moving."""
    map_id = session.get('map_id', 0)
    my_eid = session['entity_id']
    observers = tracker.get_zone_sessions(map_id, exclude_entity=my_eid)
    if not observers:
        return
    sub = build_entity_move(my_eid, cur_x, cur_y, dst_x, dst_y, speed)
    for obs in observers:
        _send_sub(obs, sub)
    await asyncio.gather(
        *(_drain(obs) for obs in observers),
        return_exceptions=True,
    )


async def _drain(session: dict) -> None:
    """Best-effort drain for an observer writer. Swallows disconnect errors."""
    try:
        await session['writer'].drain()
    except (ConnectionResetError, BrokenPipeError, OSError):
        pass

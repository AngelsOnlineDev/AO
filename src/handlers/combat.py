"""Combat handlers — targeting, skills, stop action."""

import random
import struct
import logging

from packet_builders import (
    build_entity_status,
    build_combat_action,
    build_entity_despawn,
    build_chat_msg,
    build_char_stats,
    pack_sub,
)
import database

log = logging.getLogger('handlers.combat')


async def handle_stop_action(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0009 STOP_ACTION."""
    session['dialog_state'] = None
    log.debug(f"[{addr}] C->S STOP_ACTION — cleared active state")


async def handle_target_mob(server, writer, builder, session, payload, addr):
    """Stub — real mob-click combat goes through 0x000D ENTITY_ACTION.
    0x000F is re-routed to misc.handle_heartbeat in the dispatch table
    since it turned out to be an anti-AFK tick, not a combat opcode.
    """
    return


async def handle_use_skill(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0016 USE_SKILL — explicit skill hit on a target."""
    if len(payload) < 9:
        log.debug(f"[{addr}] C->S USE_SKILL (too short)")
        return

    skill_id = payload[4]
    target_id = struct.unpack_from('<I', payload, 5)[0]
    log.info(f"[{addr}] C->S USE_SKILL id={skill_id} "
             f"target=0x{target_id:08X}")

    if target_id == 0:
        return

    registry = session.get('entity_registry') or {}
    type_id = registry.get(target_id)
    if type_id is None:
        log.info(f"[{addr}] USE_SKILL target 0x{target_id:08X} not in "
                 f"seed registry")
        return

    await _resolve_and_hit(
        server, writer, builder, session,
        target_id, type_id, skill_id, addr)


async def handle_auto_attack(server, writer, builder, session,
                              target_id, type_id, addr):
    """Called from the NPC handler when a player clicks on a combat mob.

    ENTITY_ACTION (0x000D) is the "click to interact" opcode. For mobs
    that's an auto-attack request, not a dialog open. We route through
    here with skill_id=0 (basic attack).
    """
    log.info(f"[{addr}] Auto-attack target=0x{target_id:08X} "
             f"type={type_id}")
    await _resolve_and_hit(
        server, writer, builder, session,
        target_id, type_id, skill_id=0, addr=addr)


async def _resolve_and_hit(server, writer, builder, session,
                            target_id, type_id, skill_id, addr):
    """Shared combat logic: apply damage, broadcast hit, handle death.

    1. Lazy-register mob in server.mobs with base stats from monster.xml.
    2. Roll damage from attacker's class_stats entry.
    3. Broadcast 0x0019 COMBAT_ACTION to the zone.
    4. On death, broadcast 0x001B ENTITY_DESPAWN + system chat.
    """
    entity_id = session['entity_id']
    monster_db = session.get('monster_db') or {}
    mob = server.mobs.register(target_id, type_id, monster_db)
    if not mob.alive:
        log.debug(f"[{addr}] Target '{mob.name}' already dead")
        return

    damage = _compute_damage(session)
    mob, died = server.mobs.damage(
        target_id, damage, attacker_id=entity_id)
    log.info(
        f"[{addr}] Hit '{mob.name}' for {damage} ({mob.hp}/{mob.hp_max})"
    )

    combat_sub = build_combat_action(
        source_id=entity_id,
        target_id=target_id,
        skill_id=skill_id,
        damage=damage,
        action_type=2 if skill_id else 1,
        flags=0,
    )
    combat_payload = pack_sub(combat_sub)
    writer.write(builder.build_packet(combat_payload))
    await writer.drain()
    await server.broadcast_to_zone(
        session.get('map_id', 0), combat_payload,
        exclude_entity=entity_id,
    )

    if died:
        # DIAGNOSTIC: skip the despawn packet entirely to test whether
        # our 0x001B format is what's crashing the client ~1s post-kill.
        # If crashes stop after this change, the despawn format is the
        # issue and we need to RE it. Leaving the mob visible until
        # server restart is ugly but better than a disconnect every kill.
        #
        # try:
        #     despawn_sub = build_entity_despawn(target_id)
        #     despawn_payload = pack_sub(despawn_sub)
        #     writer.write(builder.build_packet(despawn_payload))
        #     await writer.drain()
        #     await server.broadcast_to_zone(
        #         session.get('map_id', 0), despawn_payload,
        #         exclude_entity=entity_id,
        #     )
        # except Exception:
        #     log.exception(f"[{addr}] despawn failed")
        try:
            _announce_kill(writer, builder, session, mob)
        except Exception:
            log.exception(f"[{addr}] announce kill failed")
        try:
            _grant_kill_xp(
                server, writer, builder, session, mob, type_id, addr)
        except Exception:
            log.exception(f"[{addr}] grant xp failed")
        try:
            await writer.drain()
        except (ConnectionResetError, BrokenPipeError, OSError):
            pass


def _grant_kill_xp(server, writer, builder, session, mob, type_id, addr):
    """Award XP from monster.xml's 經驗價值 field and trigger level-up if
    the threshold is crossed. Replies with a chat line either way."""
    monster_db = session.get('monster_db') or {}
    mdef = monster_db.get(type_id) or {}
    xp_value = mdef.get('exp_value') or mdef.get('xp') or 0
    if xp_value <= 0:
        # Fallback so kills still feel rewarding on mobs we haven't
        # parsed XP for. Matches roughly what a low-level mob would give.
        xp_value = 5
    entity_id = session['entity_id']
    player = database.get_player(entity_id)
    if player is None:
        return
    old_xp = player['experience'] if 'experience' in player.keys() else 0
    old_level = player['level'] if 'level' in player.keys() else 1
    new_xp = old_xp + xp_value
    database.update_player_full(entity_id, experience=new_xp)
    new_level = database.level_for_xp(new_xp)
    if new_level != old_level:
        _level_up(session, entity_id, new_level)
        _send_stats_resync(writer, builder, session)
        _system_chat(writer, builder, session,
                     f"+{xp_value} XP — LEVEL UP! {old_level} -> {new_level}")
    else:
        # Refresh session cache so subsequent damage calc uses updated XP.
        session['player'] = database.get_player(entity_id)
        _system_chat(writer, builder, session, f"+{xp_value} XP")


def _level_up(session, entity_id, new_level):
    """Recompute HP/MP/stats at a new level and write back to DB."""
    from class_stats import compute_stats
    player = database.get_player(entity_id)
    class_id = player['class_id'] if player and 'class_id' in player.keys() else 0
    stats = compute_stats(class_id, new_level)
    database.update_player_full(
        entity_id,
        level=new_level,
        hp_max=stats['hp_max'],
        mp_max=stats['mp_max'],
        hp=stats['hp_max'],
        mp=stats['mp_max'],
    )
    session['player'] = database.get_player(entity_id)


def _send_stats_resync(writer, builder, session):
    """Fire-and-forget re-send of 0x0042 to refresh HP/MP bars and stats.
    Uses captured init_pkt2 tail so R.Atk/Def/etc don't blank out."""
    try:
        from world_init_builder import get_char_stats_body
        player = session.get('player')
        if not player:
            return
        hp = player['hp']
        hp_max = player['hp_max']
        mp = player['mp']
        mp_max = player['mp_max']
        sub = get_char_stats_body(hp, hp_max, mp, mp_max)
        writer.write(builder.build_packet(pack_sub(sub)))
    except Exception:
        log.debug("stats resync failed", exc_info=True)


def _system_chat(writer, builder, session, text):
    """Send a system chat line via the REAL chat opcode 0x0128. The old
    build_chat_msg (0x001E) was a session counter, not chat display — it
    silently ignored the message body and may have been contributing to
    client crashes when fired rapidly post-kill."""
    try:
        from packet_builders import build_world_chat
        sub = build_world_chat(
            sender_entity_id=session.get('entity_id', 0),
            sender_name=session.get('player_name', 'System'),
            message=f"[System] {text}",
            channel=0x0D,
        )
        writer.write(builder.build_packet(pack_sub(sub)))
    except Exception:
        log.debug("system chat failed", exc_info=True)


def _compute_damage(session: dict) -> int:
    """Compute damage based on the attacker's class_stats entry."""
    player = session.get('player')
    if player is None:
        return 5
    try:
        from class_stats import compute_stats
        keys = player.keys()
        class_id = player['class_id'] if 'class_id' in keys else 0
        level = player['level'] if 'level' in keys else 1
        stats = compute_stats(class_id, level)
        base = stats['ratk'] + stats['sp_atk']
    except Exception:
        base = 5
    # Small ±20% roll so damage feels alive
    roll = random.uniform(0.8, 1.2)
    return max(1, int(base * roll))


def _announce_kill(writer, builder, session, mob):
    """Send a chat-line announcement of a kill to the attacking player."""
    try:
        from packet_builders import build_world_chat
        msg = build_world_chat(
            sender_entity_id=session.get('entity_id', 0),
            sender_name=session.get('player_name', 'System'),
            message=f"[System] You killed {mob.name}!",
            channel=0x0D,
        )
        writer.write(builder.build_packet(pack_sub(msg)))
    except Exception:
        log.debug("Failed to build kill announcement", exc_info=True)

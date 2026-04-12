"""Combat handlers — targeting, skills, stop action."""

import random
import struct
import logging

from packet_builders import (
    build_entity_status,
    build_combat_action,
    build_entity_despawn,
    build_chat_msg,
    pack_sub,
)

log = logging.getLogger('handlers.combat')


async def handle_stop_action(server, writer, builder, session, payload, addr):
    """Handle C->S 0x0009 STOP_ACTION."""
    session['dialog_state'] = None
    log.debug(f"[{addr}] C->S STOP_ACTION — cleared active state")


async def handle_target_mob(server, writer, builder, session, payload, addr):
    """Handle C->S 0x000F TARGET_MOB."""
    if len(payload) < 8:
        log.debug(f"[{addr}] C->S TARGET_MOB (too short)")
        return

    mob_id = struct.unpack_from('<I', payload, 4)[0]
    log.info(f"[{addr}] C->S TARGET_MOB 0x{mob_id:08X}")

    session['target_mob_id'] = mob_id

    status_sub = build_entity_status(mob_id, status_a=1, status_b=1)
    pkt = builder.build_packet(pack_sub(status_sub))
    writer.write(pkt)
    await writer.drain()


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
        despawn_sub = build_entity_despawn(target_id)
        despawn_payload = pack_sub(despawn_sub)
        writer.write(builder.build_packet(despawn_payload))
        await writer.drain()
        await server.broadcast_to_zone(
            session.get('map_id', 0), despawn_payload,
            exclude_entity=entity_id,
        )
        _announce_kill(writer, builder, session, mob)
        await writer.drain()


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
        msg = build_chat_msg(
            sender_entity_id=0,
            sender_name='System',
            message=f"You killed {mob.name}!",
            pos_x=session.get('pos_x', 0),
            pos_y=session.get('pos_y', 0),
            chat_type=0x0001,
            channel=0x01,
        )
        writer.write(builder.build_packet(pack_sub(msg)))
    except Exception:
        log.debug("Failed to build kill announcement", exc_info=True)

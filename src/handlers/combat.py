"""Combat handlers — targeting, skills, stop action."""

import struct
import logging

from packet_builders import build_entity_status, build_combat_action, pack_sub

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
    """Handle C->S 0x0016 USE_SKILL."""
    if len(payload) < 9:
        log.debug(f"[{addr}] C->S USE_SKILL (too short)")
        return

    skill_id = payload[4]
    target_id = struct.unpack_from('<I', payload, 5)[0]
    entity_id = session['entity_id']
    log.info(f"[{addr}] C->S USE_SKILL id={skill_id} "
             f"target=0x{target_id:08X}")

    if target_id != 0:
        combat_sub = build_combat_action(
            source_id=entity_id,
            target_id=target_id,
            skill_id=skill_id,
            damage=100,
            action_type=2,
            flags=0,
        )
        pkt = builder.build_packet(pack_sub(combat_sub))
        writer.write(pkt)
        await writer.drain()

"""Runtime mob state — HP, death, respawn.

The seed entity registry (area_entity_data.get_seed_entity_registry) tells
us which NPC type each runtime entity_id corresponds to, so we can look up
the mob's base stats from monster.xml. This module layers runtime state
(current HP, alive/dead, respawn timer) on top of those base stats.

Damage/death flow:
    1. Player sends C->S 0x0016 USE_SKILL target=<entity>
    2. Server calls MobState.damage(entity_id, amount)
    3. If HP drops to 0, mob flips to dead state and schedules respawn
    4. Handler broadcasts 0x0019 COMBAT_ACTION and 0x001B ENTITY_DESPAWN
    5. After RESPAWN_DELAY, mob is re-registered with full HP and
       broadcast via 0x0008 NPC_SPAWN (TODO: currently only resets state;
       respawn packet broadcast is future work)

This module is deliberately in-memory only — mob HP should reset on
server restart, matching how classic MMO zones behave when the mob DB
wipes at maintenance.
"""

import logging
import time
from dataclasses import dataclass, field

log = logging.getLogger('mob_state')

RESPAWN_DELAY_SEC = 30.0  # time between death and respawn
AGGRO_TIMEOUT_SEC = 8.0   # drop aggro if no damage from the target in this long
AGGRO_ATTACK_INTERVAL_SEC = 2.0  # mob retaliates this often


@dataclass
class Mob:
    entity_id: int
    type_id: int              # monster.xml id
    name: str
    level: int
    hp_max: int
    hp: int
    # 平均攻擊 from monster.xml, defaulting to a floor so level-1 mice
    # still sting when they retaliate.
    base_attack: int = 3
    alive: bool = True
    death_time: float = 0.0   # timestamp of death, 0 if alive
    attacker_id: int = 0      # last player to hit us (for loot/exp)
    aggro_last_hit: float = 0.0    # time of last damage from attacker
    aggro_last_attack: float = 0.0 # last time we hit them back


class MobRegistry:
    """Holds the runtime Mob objects for every seed-registered entity."""

    def __init__(self):
        self._by_entity: dict[int, Mob] = {}

    def register(self, entity_id: int, type_id: int, monster_db: dict) -> Mob:
        """Create a Mob entry for a runtime entity if we don't have one.

        Called lazily the first time a player targets a mob — avoids
        pre-allocating objects for mobs that nobody touches.
        """
        existing = self._by_entity.get(entity_id)
        if existing is not None:
            return existing
        info = monster_db.get(type_id, {}) if monster_db else {}
        hp_max = int(info.get('hp', 100) or 100)
        # 平均攻擊 isn't surfaced by the current XML loader but when it is
        # we'll honor whichever key it uses. Fallback is level-scaled.
        base_attack = int(info.get('avg_attack') or info.get('atk')
                           or max(3, int(info.get('level', 1) or 1) * 2))
        mob = Mob(
            entity_id=entity_id,
            type_id=type_id,
            name=info.get('name', f'NPC#{type_id}'),
            level=int(info.get('level', 1) or 1),
            hp_max=hp_max,
            hp=hp_max,
            base_attack=base_attack,
        )
        self._by_entity[entity_id] = mob
        return mob

    def get(self, entity_id: int) -> Mob | None:
        return self._by_entity.get(entity_id)

    def damage(self, entity_id: int, amount: int,
               attacker_id: int = 0) -> tuple[Mob | None, bool]:
        """Apply damage. Returns (mob, died_this_hit).

        `mob` is None if the entity is not a known mob. `died_this_hit`
        is True if this hit brought the mob from alive → dead.
        """
        mob = self._by_entity.get(entity_id)
        if mob is None or not mob.alive:
            return mob, False
        died = False
        mob.hp -= max(0, amount)
        if attacker_id:
            mob.attacker_id = attacker_id
            mob.aggro_last_hit = time.time()
        if mob.hp <= 0:
            mob.hp = 0
            mob.alive = False
            mob.death_time = time.time()
            died = True
            log.info(
                f"Mob 0x{entity_id:08X} '{mob.name}' killed by "
                f"0x{attacker_id:08X}"
            )
        return mob, died

    def tick_respawns(self, now: float | None = None) -> list[Mob]:
        """Respawn any dead mobs whose timer has elapsed.

        Returns the list of mobs that respawned on this tick so the
        caller can broadcast NPC_SPAWN packets. Call periodically
        (e.g. from the keepalive loop).
        """
        if now is None:
            now = time.time()
        respawned: list[Mob] = []
        for mob in self._by_entity.values():
            if mob.alive:
                continue
            if now - mob.death_time < RESPAWN_DELAY_SEC:
                continue
            mob.hp = mob.hp_max
            mob.alive = True
            mob.death_time = 0.0
            respawned.append(mob)
            log.info(f"Mob 0x{mob.entity_id:08X} '{mob.name}' respawned")
        return respawned

    def alive_count(self) -> int:
        return sum(1 for m in self._by_entity.values() if m.alive)

    def aggroed_mobs(self, now: float | None = None) -> list[Mob]:
        """Return every mob with a live aggro lock that's due to swing.

        A mob is 'live-aggro' if it's alive, has an attacker_id, and the
        attacker dealt damage within AGGRO_TIMEOUT_SEC. Returned mobs
        also satisfy the attack-cooldown (last swing older than
        AGGRO_ATTACK_INTERVAL_SEC). Stale aggro is silently dropped.
        """
        if now is None:
            now = time.time()
        ready: list[Mob] = []
        for mob in self._by_entity.values():
            if not mob.alive or not mob.attacker_id:
                continue
            if now - mob.aggro_last_hit > AGGRO_TIMEOUT_SEC:
                # Drop aggro so the mob stops chasing after fleeing players.
                mob.attacker_id = 0
                continue
            if now - mob.aggro_last_attack >= AGGRO_ATTACK_INTERVAL_SEC:
                ready.append(mob)
        return ready

    def mark_attacked(self, mob: Mob, now: float | None = None):
        mob.aggro_last_attack = now if now is not None else time.time()

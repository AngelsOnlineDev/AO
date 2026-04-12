"""Per-class base stats and level scaling.

Class IDs match the game's setting/eng/class.xml (authoritative):
    0   Novice
    1   Priest
    2   Summoner
    3   Wizard
    4   Magician
    5   Protector
    6   Warrior
    7   Swordsman
    8   Spearman
    9   Archer
    10  Weaponsmith
    11  Armorsmith
    12  Tailor
    13  Technician
    14  Alchemist (deleted)
    15  Chef
    16  Wizard Jr.
    17  M.Soldier
    18  MagicBowman
    19  Miner
    20  Producer

Stats are derived from `compute_stats(class_id, level)`. Currently only
class 0 (Novice) and the six main combat classes (1-6) have meaningful
stats; crafters and NPC placeholders fall back to Novice numbers.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassBase:
    name: str
    hp_base: int
    hp_per_level: int
    mp_base: int
    mp_per_level: int
    ratk_base: int
    ratk_per_level: int
    latk_base: int
    latk_per_level: int
    dfs_base: int
    dfs_per_level: int
    sp_atk_base: int
    sp_atk_per_level: int
    sp_dfs_base: int
    sp_dfs_per_level: int
    rigor_base: int
    agility_base: int
    critical_base: int
    soul_base: int


_NOVICE = ClassBase(
    name="Novice",
    hp_base=50,  hp_per_level=6,
    mp_base=30,  mp_per_level=4,
    ratk_base=5, ratk_per_level=1,
    latk_base=0, latk_per_level=0,
    dfs_base=3,  dfs_per_level=1,
    sp_atk_base=2, sp_atk_per_level=0,
    sp_dfs_base=2, sp_dfs_per_level=0,
    rigor_base=5, agility_base=5, critical_base=2, soul_base=3,
)


_CLASS_TABLE: dict[int, ClassBase] = {
    0: _NOVICE,
    1: ClassBase(  # Priest — healing / support, high MP
        name="Priest",
        hp_base=60,  hp_per_level=8,
        mp_base=80,  mp_per_level=12,
        ratk_base=4, ratk_per_level=1,
        latk_base=0, latk_per_level=0,
        dfs_base=4,  dfs_per_level=1,
        sp_atk_base=8, sp_atk_per_level=2,
        sp_dfs_base=6, sp_dfs_per_level=2,
        rigor_base=6, agility_base=5, critical_base=3, soul_base=10,
    ),
    2: ClassBase(  # Summoner — curses/summons, cloth armor
        name="Summoner",
        hp_base=55,  hp_per_level=7,
        mp_base=85,  mp_per_level=13,
        ratk_base=3, ratk_per_level=1,
        latk_base=0, latk_per_level=0,
        dfs_base=3,  dfs_per_level=1,
        sp_atk_base=10, sp_atk_per_level=3,
        sp_dfs_base=5, sp_dfs_per_level=2,
        rigor_base=5, agility_base=5, critical_base=4, soul_base=9,
    ),
    3: ClassBase(  # Wizard — chaos/destruction, high Spl Atk
        name="Wizard",
        hp_base=50,  hp_per_level=6,
        mp_base=90,  mp_per_level=14,
        ratk_base=3, ratk_per_level=1,
        latk_base=0, latk_per_level=0,
        dfs_base=3,  dfs_per_level=1,
        sp_atk_base=12, sp_atk_per_level=3,
        sp_dfs_base=4, sp_dfs_per_level=1,
        rigor_base=5, agility_base=5, critical_base=5, soul_base=8,
    ),
    4: ClassBase(  # Magician — earth/charm, some melee
        name="Magician",
        hp_base=65,  hp_per_level=8,
        mp_base=75,  mp_per_level=11,
        ratk_base=6, ratk_per_level=2,
        latk_base=0, latk_per_level=0,
        dfs_base=5,  dfs_per_level=1,
        sp_atk_base=9, sp_atk_per_level=2,
        sp_dfs_base=5, sp_dfs_per_level=1,
        rigor_base=6, agility_base=6, critical_base=4, soul_base=7,
    ),
    5: ClassBase(  # Protector — tank, shield + heavy armor
        name="Protector",
        hp_base=110, hp_per_level=16,
        mp_base=20,  mp_per_level=2,
        ratk_base=7, ratk_per_level=2,
        latk_base=0, latk_per_level=0,
        dfs_base=12, dfs_per_level=3,
        sp_atk_base=1, sp_atk_per_level=0,
        sp_dfs_base=4, sp_dfs_per_level=1,
        rigor_base=12, agility_base=5, critical_base=3, soul_base=3,
    ),
    6: ClassBase(  # Warrior — axe, high damage, heavy armor
        name="Warrior",
        hp_base=100, hp_per_level=14,
        mp_base=20,  mp_per_level=2,
        ratk_base=10, ratk_per_level=3,
        latk_base=0, latk_per_level=0,
        dfs_base=8, dfs_per_level=2,
        sp_atk_base=1, sp_atk_per_level=0,
        sp_dfs_base=2, sp_dfs_per_level=0,
        rigor_base=10, agility_base=6, critical_base=4, soul_base=3,
    ),
    7: ClassBase(  # Swordsman — sword burst damage, heavy armor
        name="Swordsman",
        hp_base=95,  hp_per_level=13,
        mp_base=25,  mp_per_level=3,
        ratk_base=11, ratk_per_level=3,
        latk_base=0, latk_per_level=0,
        dfs_base=7, dfs_per_level=2,
        sp_atk_base=1, sp_atk_per_level=0,
        sp_dfs_base=2, sp_dfs_per_level=0,
        rigor_base=9, agility_base=7, critical_base=5, soul_base=3,
    ),
    8: ClassBase(  # Spearman — piercing, heavy armor
        name="Spearman",
        hp_base=95,  hp_per_level=13,
        mp_base=25,  mp_per_level=3,
        ratk_base=11, ratk_per_level=3,
        latk_base=0, latk_per_level=0,
        dfs_base=7, dfs_per_level=2,
        sp_atk_base=1, sp_atk_per_level=0,
        sp_dfs_base=2, sp_dfs_per_level=0,
        rigor_base=9, agility_base=7, critical_base=5, soul_base=3,
    ),
    9: ClassBase(  # Archer — long bow, ranged, light armor
        name="Archer",
        hp_base=75,  hp_per_level=10,
        mp_base=40,  mp_per_level=5,
        ratk_base=9, ratk_per_level=2,
        latk_base=0, latk_per_level=0,
        dfs_base=5, dfs_per_level=1,
        sp_atk_base=2, sp_atk_per_level=0,
        sp_dfs_base=3, sp_dfs_per_level=1,
        rigor_base=6, agility_base=10, critical_base=7, soul_base=5,
    ),
}


def get_class(class_id: int) -> ClassBase:
    """Return the ClassBase for a class_id, falling back to Novice."""
    return _CLASS_TABLE.get(class_id, _NOVICE)


def class_name(class_id: int) -> str:
    return get_class(class_id).name


def compute_stats(class_id: int, level: int) -> dict[str, int]:
    """Compute a character's full stat block from class_id + level.

    Level is clamped to >=1. Returns a dict with keys:
      hp_max mp_max ratk latk dfs sp_atk sp_dfs rigor agility critical soul
    """
    c = get_class(class_id)
    lvl = max(1, level)
    extra = lvl - 1
    return {
        'hp_max':    c.hp_base + extra * c.hp_per_level,
        'mp_max':    c.mp_base + extra * c.mp_per_level,
        'ratk':      c.ratk_base + extra * c.ratk_per_level,
        'latk':      c.latk_base + extra * c.latk_per_level,
        'dfs':       c.dfs_base + extra * c.dfs_per_level,
        'sp_atk':    c.sp_atk_base + extra * c.sp_atk_per_level,
        'sp_dfs':    c.sp_dfs_base + extra * c.sp_dfs_per_level,
        'rigor':     c.rigor_base,
        'agility':   c.agility_base,
        'critical':  c.critical_base,
        'soul':      c.soul_base,
    }

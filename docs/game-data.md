# Game Data Reference

Quick-reference for all known IDs, constants, and magic numbers used by the server.

---

## Table of Contents

1. [Maps & Spawn Points](#maps--spawn-points)
2. [Classes](#classes)
3. [NPCs](#npcs)
4. [Monsters](#monsters)
5. [Quests](#quests)
6. [Zone Warp Connections](#zone-warp-connections)
7. [Chat Channels](#chat-channels)
8. [Entity ID Allocation](#entity-id-allocation)
9. [Action & Trigger Types](#action--trigger-types)
10. [Player Defaults](#player-defaults)
11. [Movement Constants](#movement-constants)

---

## Maps & Spawn Points

### Starting Map

| Key | Value |
|-----|-------|
| START_MAP_ID | 2 (Angel Lyceum / Eden) |
| DEFAULT_SPAWN | (1040, 720) |

### Known Spawn Points

Defined in `config.py` as `MAP_SPAWN_POINTS[(map_id, spawn_index)]`:

| Map ID | Spawn | Coordinates | Name (if known) |
|--------|-------|-------------|------------------|
| 2 | 0 | (1040, 720) | Angel Lyceum / Eden |
| 3 | 0 | (500, 500) | — |
| 52 | 0 | (500, 500) | — |
| 63 | 0 | (500, 500) | — |
| 63 | 1 | (800, 500) | — |
| 63 | 2 | (500, 800) | — |
| 64 | 0 | (500, 500) | — |
| 65 | 0 | (500, 500) | — |
| 66 | 0 | (500, 500) | — |
| 66 | 1 | (800, 500) | — |
| 66 | 2 | (500, 800) | — |
| 81 | 0 | (500, 500) | — |

### Zone List (Init Packet 0x018E)

Zones sent to client during initialization (10 active slots):

| Slot | Zone ID | Hex |
|------|---------|-----|
| 0 | 60 | 0x3C |
| 1 | 61 | 0x3D |
| 2 | 62 | 0x3E |
| 10 | 41 | 0x29 |
| 11 | 42 | 0x2A |
| 12 | 45 | 0x2D |
| 13 | 50 | 0x32 |
| 14 | 51 | 0x33 |
| 15 | 53 | 0x35 |
| 16 | 56 | 0x38 |

---

## Classes

From `game_xml/setting/eng/class.xml`:

| ID | Name | Description |
|----|------|-------------|
| 0 | Novice | Starting class |
| 1 | Priest | Healing/support, cloth armor |
| 2 | Summoner | Curse/summoning, cloth armor |
| 3 | Wizard | Chaos/destruction, cloth armor |
| 4 | Magician | Earth spells, melee hybrid, cloth armor |
| 5 | Protector | Tank, shield defense, heavy armor |
| 6 | Warrior | Axe wielder, heavy armor |
| 7 | Swordsman | Sword skills, heavy armor |
| 8 | Spearman | Spear skills, heavy armor |
| 9 | Archer | Bow skills, ranged, light armor |
| 10 | Weaponsmith | Forge weapons, mining, light armor |
| 11 | Armorsmith | Make armor, collecting, light armor |
| 12 | Tailor | Cloth armor, backpacks, fishing, light armor |
| 13 | Technician | Robot accessories, staves, leather armor |
| 14 | Alchemist | (Deleted class) |
| 15 | Chef | Cooking, nutrition, light armor |
| 16 | Wizard Jr. | Junior wizard |
| 17 | M.Soldier | Magic soldier |
| 18 | MagicBowman | Default class in some code paths |
| 19 | Miner | Gathering class |
| 20 | Producer | Crafting class |

---

## NPCs

### Hardcoded NPC Behaviors

These NPCs have special server-side logic in `handlers/npc.py`:

| Type ID | Name | Behavior | Details |
|---------|------|----------|---------|
| 2006 | Census Angel | quest_npc | Quest 100, Dialog 1 (class selection) |
| 2429 | Merchant | shop | Shop ID 1 |
| 8804 | Aurora Totem | totem | Teleporter |
| 1553 | House Pickets | gate | Warps to map 3, spawn 0 |
| 1554 | Gaoler Angel | gate | Warps to map 3, spawn 0 |
| 1938 | Dark City Totem | totem | Teleporter |
| 1940 | Breeze Totem | totem | Teleporter |

### NPC ID Ranges

| Range | Type |
|-------|------|
| < 1500 | Monsters / objects |
| 1500–65535 | NPCs (from npc.xml) |

### Angel Lyceum NPCs (Map 2)

Selected NPCs from `npc.xml` (IDs 1500–1599):

| ID | Name | Sprite | Role |
|----|------|--------|------|
| 1500 | Player | 40001 | Player template |
| 1501 | Michael | 40001 | President |
| 1502 | Cupid | 40006 | — |
| 1503 | Magic Professor | 40003 | Magic tutorial |
| 1504 | Shopkeeper | 40004 | General shop |
| 1505 | Battle Professor | 40005 | Combat tutorial |
| 1506 | Skill Angel | 40002 | Skill tutorial |
| 1507 | Fat Laborer | 40007 | — |
| 1508 | Fat Cook | 40008 | — |
| 1512 | Guard | 40012 | — |
| 1513 | Knight | 40013 | — |
| 1541 | Mine Professor | 40041 | Mining tutorial |
| 1542 | Weapon Lecturer | 40042 | Weaponsmithing |
| 1543 | Armor Lecturer | 40043 | Armorsmithing |
| 1544 | Sewing Lecturer | 40044 | Tailoring |
| 1545 | Art Lecturer | 40045 | Technician |
| 1546 | Cooking Lecturer | 40046 | Chef |
| 1547 | Recipe Seller | 40004 | Recipes |
| 1548 | Reward Angel | 40048 | Rewards |
| 1549 | Director Wolay | 40049 | — |
| 1550 | Ironsmith | 40004 | — |
| 1551 | Chief Director | 40051 | — |
| 1552 | Repair Angel | 40052 | Equipment repair |
| 1553 | House Pickets | 40053 | Gate to map 3 |
| 1554 | Gaoler Angel | 40053 | Gate to map 3 |
| 1555 | Magic Seller | 40004 | Magic items |
| 1563 | Aurora Guard | 40012 | — |
| 1586 | Newbie Timi | 40012 | — |
| 1594 | Weaponsmith | 40007 | — |
| 1595 | Bow Seller | 40050 | — |

---

## Monsters

From `game_xml/monster.xml` (selected entries):

| ID | Name | Level | HP | Sprite |
|----|------|-------|----|--------|
| 1 | Wind Elf | 4 | 162 | 42055 |
| 2 | Fire Elf | 7 | 300 | 42056 |
| 3 | Water Elf | 4 | 162 | 42057 |
| 4 | Earth Elf | 7 | 300 | 42058 |
| 5 | Mutant Earth Elf | 6 | 202 | 42001 |
| 6 | Fire Goblin | 6 | 202 | 42101 |
| 7 | Lily | 1 | 100 | 42041 |
| 19 | Slarm | 1 | 118 | 42107 |

See `data/game_xml/monster.xml` for the full monster table.

---

## Quests

From `game_xml/quest.xml`:

### Tutorial Quests (Angel Lyceum)

| ID | Name | Repeatable |
|----|------|------------|
| 100 | Registration at Angels' Tutor | No |
| 101 | Freshman's signing for taking courses | No |
| 102 | Accumulation schedule of students' grades | No |
| 103 | Choosing country | No |
| 106 | Exercise for lumbering | Yes |
| 107 | Exercise for digging | Yes |
| 108 | Exercise for collecting | Yes |
| 109 | Exercise for fishing | Yes |
| 110 | Exercise for manufacturing weapon | Yes |
| 111 | Exercise for manufacturing armor | Yes |
| 112 | Exercise for sewing | Yes |
| 113 | Exercise for art | Yes |
| 114 | Exercise for cooking | Yes |
| 115 | Battle — Primary course | Yes |
| 116 | Battle — Intermediate course | Yes |
| 117 | Battle — Advanced course | Yes |
| 118 | Magic — Primary course | Yes |
| 119 | Magic — Intermediate course | Yes |
| 120 | Magic — Advanced course | Yes |
| 123 | Training top student | No |
| 124 | Defeat Magic Armor Header | No |
| 125 | Knights of Operation Elf | No |
| 126 | Last General | No |

### City Registration Quests

| ID | Name |
|----|------|
| 127 | Register at Aurora City Angel |
| 128 | Register at Dark City Angel |
| 129 | Register at Iron Castle Angel |
| 130 | Register at Breeze Jungle |

See `data/game_xml/quest.xml` for the full quest list (250+ quests).

---

## Zone Warp Connections

Currently implemented warp routes:

```
Map 2 (Angel Lyceum)
├── NPC 1553 (House Pickets) ──→ Map 3, spawn 0
└── NPC 1554 (Gaoler Angel) ──→ Map 3, spawn 0
```

Additional warps can be triggered through NPC dialog trees when the dialog action type is 37 (zone warp). The dialog system reads these from `game_xml/` event files.

---

## Chat Channels

### Local Chat (opcode 0x001E)

| Channel Byte | Name |
|--------------|------|
| 0x01 | System / NPC speech |

### World Chat (opcode 0x0128)

| Channel Byte | Name |
|--------------|------|
| 0x02 | Party |
| 0x0A | World |
| 0x0B | Shout |

---

## Entity ID Allocation

### Runtime Entity IDs

- Base start: `0x12341000`
- Incremented by 1 for each spawned entity
- Format: `(high_word << 16) | low_word`

### Examples

| Entity | Runtime ID | NPC Type |
|--------|-----------|----------|
| Census Angel | 0x13B00E85 | 2006 |
| House Pickets | 0x127F0D54 | 1553 |

### Entity Registry

The server maintains a mapping of `runtime_entity_id → npc_type_id` built at startup from area entity packets. This is used to resolve NPC clicks (opcode 0x000D).

---

## Action & Trigger Types

From `map_loader.py`:

### Trigger Types

| Value | Name | Description |
|-------|------|-------------|
| 2 | TALK | Player clicks/interacts with entity |

### Action Types

| Value | Name | Description |
|-------|------|-------------|
| 25 | DIALOG | Start a dialog tree |
| 37 | WARP | Zone transfer (params: map_id, spawn_point, flag) |

### Entity Record

MPC map files store entities in **74-byte records** parsed by `map_loader.py`.

---

## Player Defaults

From `database.py` — values for newly created players:

| Field | Default |
|-------|---------|
| HP | 294 |
| HP max | 294 |
| MP | 280 |
| MP max | 280 |
| Gold | 500 |
| Class ID | 0 (Novice) |
| Level | 1 |
| Map ID | 2 |
| Position | (1040, 720) |

---

## Movement Constants

From `config.py`:

| Constant | Value | Description |
|----------|-------|-------------|
| MOVE_SPEED | 110 | Character movement speed (0x6E) |
| MAX_MOVE_STEP | 200 | Max pixels per movement segment |
| KEEPALIVE_INTERVAL | 1.0 | Seconds between keepalive packets |

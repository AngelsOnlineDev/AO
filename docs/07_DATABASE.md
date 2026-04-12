# Database schema

SQLite database at `data/angels.db`. WAL mode, foreign keys on. Schema is owned by [database.py](../src/database.py) — that file is the source of truth; this doc lags.

## Tables

### accounts

```sql
CREATE TABLE accounts (
    id             INTEGER PRIMARY KEY,
    username       TEXT UNIQUE NOT NULL,
    password_hash  TEXT NOT NULL,
    salt           TEXT NOT NULL,
    created_at     TEXT DEFAULT (datetime('now'))
);
```

| Column | Description |
|---|---|
| `username` | login name, case-sensitive, unique |
| `password_hash` | hex string of the raw hash bytes the client sends. We store whatever bytes arrive — we do **not** compute our own hash ([03_LOGIN_FLOW.md](03_LOGIN_FLOW.md) explains why) |
| `salt` | 16-byte random hex string. Currently unused for verification but kept for future migration to a real hash scheme |

### players

```sql
CREATE TABLE players (
    id          INTEGER PRIMARY KEY,
    account_id  INTEGER REFERENCES accounts(id),
    entity_id   INTEGER UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    level       INTEGER DEFAULT 1,
    class_id    INTEGER DEFAULT 0,
    pos_x       INTEGER DEFAULT 1040,
    pos_y       INTEGER DEFAULT 720,
    map_id      INTEGER DEFAULT 0,
    party_name  TEXT    DEFAULT '',
    hp          INTEGER DEFAULT 294,
    hp_max      INTEGER DEFAULT 294,
    mp          INTEGER DEFAULT 280,
    mp_max      INTEGER DEFAULT 280,
    gold        INTEGER DEFAULT 500,
    app0        INTEGER DEFAULT 0,
    app1        INTEGER DEFAULT 0,
    app2        INTEGER DEFAULT 0,
    app3        INTEGER DEFAULT 0,
    app4        INTEGER DEFAULT 0
);
```

| Column | Notes |
|---|---|
| `entity_id` | random `0x10000000..0x7FFFFFFF`, unique. Avoids NPC runtime ID ranges. |
| `class_id` | 0 = Novice, 1-9 = combat classes, 10-15 = crafting classes. See [06_HANDLERS.md](06_HANDLERS.md) for the table. |
| `pos_x / pos_y` | pixel coordinates (tile × 32). Defaults match the Soualz capture's spawn. |
| `map_id` | **DB default 0**, but [world_server.py:221](../src/world_server.py#L221) overrides to `129` at world-auth time regardless of this value. See [03_LOGIN_FLOW.md](03_LOGIN_FLOW.md). |
| `hp_max / mp_max` | Defaults match Soualz (Priest level 18). New low-level characters get overwritten by [class_stats.py](../src/class_stats.py) during init packet patching, but the DB default is still these high values. ❓ Should migrate defaults. |
| `app0..app4` | Appearance bytes (skin/hair/etc). Written at init_pkt1 offset base+55..59 by [world_init_builder.py](../src/world_init_builder.py). Sarah is all-zeros, Sandra is `(0,0,0,5,22)`, etc. |
| `party_name` | Carried in `0x014A` during init; not updated at runtime yet. |

**Missing that we'll need**:

- ❌ **`equipment` table** — no storage for equipped items. Remote player spawn (`0x0001`) carries 8 equipment slot IDs that drive the visible outfit, but we send all zeros. This is why every remote player shows as a naked default model. See [04_WORLD_SERVER.md](04_WORLD_SERVER.md).
- ❌ **`items` / `inventory`** — no item system at all. The client sends `0x0012 BUY_SELL` but we have no items to sell.
- ❌ **`faction`** — profile offset `base+60` is faction (Steel / Wind / Fire), not class_id. We leave it at whatever the capture had. Should be a column.
- ❌ **`experience`** — no XP tracking.
- ❌ **`skills`** — every character gets Soualz's skill loadout replayed from init_pkt4.

### player_settings

```sql
CREATE TABLE player_settings (
    id          INTEGER PRIMARY KEY,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    marker      INTEGER NOT NULL,
    setting_id  INTEGER NOT NULL,
    value_lo    INTEGER DEFAULT 0,
    value       INTEGER DEFAULT 0,
    value_hi    INTEGER DEFAULT 0,
    packet_num  INTEGER DEFAULT 1,
    UNIQUE(player_id, marker, setting_id, packet_num)
);
```

Maps one-to-one to `0x001D` entity-setting sub-messages sent during world init. ❓ Most rows are replayed verbatim from the Soualz capture; we've never modified a setting at runtime.

### npcs / entities / mobs / positions

These four tables are **passive stores** of data parsed from the captured seed packets (`area_pkt*.hex`, `init_pkt*.hex`). They're populated at server startup by `area_entity_data.load_all_seeds()` and read by the area-packet builder during zone transfers.

```sql
-- npcs: stores 0x0008 NPC spawn records
CREATE TABLE npcs (
    id, npc_id_lo, npc_id_hi, unk1, pos_x, pos_y,
    name, extra BLOB,  -- 34B: sprite_id @5-6, npc_type_id @16-17
    source, area_packet_num, msg_order
);

-- entities: stores 0x000E static entity records
CREATE TABLE entities (
    id, entity_id_lo, entity_id_hi, unk1, pos_x, pos_y,
    name_bytes BLOB, tail_bytes BLOB,
    source, area_packet_num, msg_order
);

-- mobs: stores 0x000F monster spawn records (seed data only — does NOT
--       track runtime mob HP; that lives in mob_state.py MobRegistry)
CREATE TABLE mobs (
    id, mob_id_lo, mob_id_hi, unk1, pos_x, pos_y,
    name, name_bytes BLOB, tail_bytes BLOB,
    source, area_packet_num, msg_order
);

-- positions: stores 0x0005 position markers from init/area packets
CREATE TABLE positions (
    id, entity_id_lo, entity_id_hi,
    x1, y1, x2, y2, speed,
    packet_num, source, area_packet_num, msg_order
);
```

❓ These are essentially a cache of the seed packet contents in a queryable form. The real use is regenerating area packets per zone transfer; if we ever parse maps directly from PAK files, these tables become obsolete.

**Important**: the `mobs` table is **not** runtime mob HP. Mob state (current HP, respawn deadlines) lives in [mob_state.py](../src/mob_state.py) `MobRegistry` — in-memory, lost on server restart. ❓ Long-term we'd persist respawn state but it's not a priority.

## Key operations

### Accounts

```python
create_account(username, password_hash_bytes) -> Row
get_account(username) -> Row | None
verify_password(account, hash_bytes) -> bool    # byte-for-byte compare
```

### Characters

```python
create_character(account_id, name, class_id=0, appearance=(0,0,0,0,0)) -> Row
delete_character(entity_id)                     # used by Phase-4 DELETE
get_player(entity_id) -> Row | None
get_player_by_name(name) -> Row | None
get_characters_for_account(account_id) -> list[Row]
```

`create_character` generates a random `entity_id` in `0x10000000..0x7FFFFFFF` and retries on collision.

### Runtime state updates

```python
update_player_position(entity_id, x, y)            # every movement packet
update_player_map(entity_id, map_id, x, y)         # on zone transfer
update_player_class(entity_id, class_id)           # Census Angel
update_player_stats(entity_id, hp, mp)             # combat (currently unused)
```

Movement writes to the DB on every packet. That's hot — ❓ long-term we should batch via a periodic flush, but with single-digit players it's fine.

## Pending-names file

Not a DB table, but relevant: [data/pending_names.txt](../data/pending_names.txt) holds candidate character names. The client only sends an **MD5 hex of the name** during character creation — to resolve it we MD5 each candidate in this file and match. Any name the user wants to use has to be pre-added to the file.

❓ This is ugly but workable. The proper fix is figuring out how the real server decoded the hash — possibly the client actually sends both and we're misreading the packet.

## Known unknowns

- ❌ The client's password hash algorithm. We round-trip bytes instead of hashing server-side.
- ❌ How character *creation* should persist the 35 "extra" bytes of the CREATE packet body (we ignore them).
- ❌ Why the server needs `npcs / entities / mobs / positions` SQL tables at all — they were an early design choice when we thought we'd generate spawns from SQL instead of replaying capture bytes. Dead weight now but removing them would mean rewriting area packet generation.

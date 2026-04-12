# Database Schema

SQLite database at `data/angels.db`. Uses WAL mode and foreign keys.

## Tables

### accounts

```sql
CREATE TABLE accounts (
    id INTEGER PRIMARY KEY,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    salt TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);
```

| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Auto-increment primary key |
| username | TEXT | Login name (unique, case-sensitive) |
| password_hash | TEXT | SHA-256 hex digest of `salt + password` |
| salt | TEXT | 16-byte random salt (hex-encoded, 32 chars) |
| created_at | TEXT | ISO 8601 creation timestamp |

**Password hashing**: `SHA256((salt + password).encode('utf-8')).hexdigest()`

### players

```sql
CREATE TABLE players (
    id INTEGER PRIMARY KEY,
    account_id INTEGER REFERENCES accounts(id),
    entity_id INTEGER UNIQUE NOT NULL,
    name TEXT NOT NULL,
    level INTEGER DEFAULT 1,
    class_id INTEGER DEFAULT 0,
    pos_x INTEGER DEFAULT 1040,
    pos_y INTEGER DEFAULT 720,
    map_id INTEGER DEFAULT 0,
    party_name TEXT DEFAULT '',
    hp INTEGER DEFAULT 294,
    hp_max INTEGER DEFAULT 294,
    mp INTEGER DEFAULT 280,
    mp_max INTEGER DEFAULT 280,
    gold INTEGER DEFAULT 500
);
```

| Column | Type | Default | Description |
|--------|------|---------|-------------|
| id | INTEGER | auto | Primary key |
| account_id | INTEGER | - | FK to accounts.id |
| entity_id | INTEGER | random | Unique 32-bit entity ID (0x10000000-0x7FFFFFFF) |
| name | TEXT | - | Character display name |
| level | INTEGER | 1 | Current level |
| class_id | INTEGER | 0 | 0=Novice, 1-15=class (see Census Angel) |
| pos_x | INTEGER | 1040 | X position in tile-pixels |
| pos_y | INTEGER | 720 | Y position in tile-pixels |
| map_id | INTEGER | 0 | Current zone (0=unset, defaults to 2) |
| party_name | TEXT | '' | Guild/party name |
| hp | INTEGER | 294 | Current HP |
| hp_max | INTEGER | 294 | Maximum HP |
| mp | INTEGER | 280 | Current MP |
| mp_max | INTEGER | 280 | Maximum MP |
| gold | INTEGER | 500 | Currency |

### player_settings

```sql
CREATE TABLE player_settings (
    id INTEGER PRIMARY KEY,
    player_id INTEGER NOT NULL REFERENCES players(id),
    marker INTEGER NOT NULL,
    setting_id INTEGER NOT NULL,
    value_lo INTEGER NOT NULL DEFAULT 0,
    value INTEGER NOT NULL DEFAULT 0,
    value_hi INTEGER NOT NULL DEFAULT 0,
    packet_num INTEGER NOT NULL DEFAULT 1,
    UNIQUE(player_id, marker, setting_id, packet_num)
);
```

Maps to 0x001D entity setting sub-messages (16 bytes each). Used for character traits sent during world init.

### npcs

```sql
CREATE TABLE npcs (
    id INTEGER PRIMARY KEY,
    npc_id_lo INTEGER NOT NULL,
    npc_id_hi INTEGER NOT NULL,
    unk1 INTEGER DEFAULT 1,
    pos_x INTEGER NOT NULL,
    pos_y INTEGER NOT NULL,
    name TEXT NOT NULL,
    extra BLOB NOT NULL,
    source TEXT NOT NULL DEFAULT 'init',
    area_packet_num INTEGER,
    msg_order INTEGER
);
```

Stores 0x0008 NPC spawn records. `extra` is a 34-byte blob containing sprite_id (bytes 5-6) and npc_type_id (bytes 16-17).

### entities

```sql
CREATE TABLE entities (
    id INTEGER PRIMARY KEY,
    entity_id_lo INTEGER NOT NULL,
    entity_id_hi INTEGER NOT NULL,
    unk1 INTEGER DEFAULT 0,
    pos_x INTEGER NOT NULL,
    pos_y INTEGER NOT NULL,
    name_bytes BLOB NOT NULL,
    tail_bytes BLOB NOT NULL,
    source TEXT NOT NULL DEFAULT 'init',
    area_packet_num INTEGER,
    msg_order INTEGER
);
```

Stores 0x000E entity spawn records (static/environmental entities).

### mobs

```sql
CREATE TABLE mobs (
    id INTEGER PRIMARY KEY,
    mob_id_lo INTEGER NOT NULL,
    mob_id_hi INTEGER NOT NULL,
    unk1 INTEGER DEFAULT 0,
    pos_x INTEGER NOT NULL,
    pos_y INTEGER NOT NULL,
    name TEXT NOT NULL,
    name_bytes BLOB NOT NULL,
    tail_bytes BLOB NOT NULL,
    source TEXT NOT NULL DEFAULT 'init',
    area_packet_num INTEGER,
    msg_order INTEGER
);
```

Stores 0x000F monster spawn records.

### positions

```sql
CREATE TABLE positions (
    id INTEGER PRIMARY KEY,
    entity_id_lo INTEGER NOT NULL,
    entity_id_hi INTEGER NOT NULL,
    x1 INTEGER NOT NULL,
    y1 INTEGER NOT NULL,
    x2 INTEGER NOT NULL,
    y2 INTEGER NOT NULL,
    speed INTEGER NOT NULL,
    packet_num INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'init',
    area_packet_num INTEGER,
    msg_order INTEGER
);
```

Stores 0x0005 position records from init/area packets.

## Key Operations

### Account Management

```python
create_account(username, password) -> Row
    # Generates random salt, hashes password, inserts

get_account(username) -> Row | None
    # Lookup by username

verify_password(account, password) -> bool
    # SHA256(salt + password) == stored hash
```

### Character Management

```python
create_character(account_id, name, class_id=0) -> Row
    # Generates random entity_id (0x10000000-0x7FFFFFFF)
    # Inserts with default stats

get_player(entity_id) -> Row | None
    # Lookup by entity_id

get_player_by_name(name) -> Row | None
    # Lookup by character name

get_characters_for_account(account_id) -> list[Row]
    # All characters for an account
```

### State Updates

```python
update_player_position(entity_id, x, y)
    # Update pos_x, pos_y

update_player_map(entity_id, map_id, x, y)
    # Update map_id, pos_x, pos_y (zone transfer)

update_player_class(entity_id, class_id)
    # Update class_id (Census Angel selection)

update_player_stats(entity_id, hp, mp)
    # Update hp, mp
```

## Entity ID Generation

```python
entity_id = random.randint(0x10000000, 0x7FFFFFFF)
# Retry if collision (checked via get_player)
```

Entity IDs must be unique across all players. The range avoids collision with NPC runtime IDs (which use 0x1234xxxx pattern).

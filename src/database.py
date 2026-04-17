"""SQLite database for Angels Online private server.

Stores accounts, player characters, NPC definitions, entity spawns,
and positions.

Tables use a 'source' column to distinguish init-phase data from
area-phase data. Area data also tracks which area packet and
sub-message order it came from.
"""

import hashlib
import os
import random
import sqlite3
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_PATH = DB_DIR / "angels.db"

_conn: sqlite3.Connection | None = None


def get_connection() -> sqlite3.Connection:
    """Get or create the singleton database connection."""
    global _conn
    if _conn is None:
        DB_DIR.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(str(DB_PATH))
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _create_tables(_conn)
    return _conn


def close():
    """Close the database connection."""
    global _conn
    if _conn is not None:
        _conn.close()
        _conn = None


def _create_tables(conn: sqlite3.Connection):
    """Create all tables if they don't exist."""
    conn.executescript("""
        -- Accounts
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- Player characters (linked to accounts)
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY,
            account_id INTEGER REFERENCES accounts(id),
            entity_id INTEGER UNIQUE NOT NULL,
            name TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            class_id INTEGER DEFAULT 0,
            pos_x INTEGER DEFAULT 1040,
            pos_y INTEGER DEFAULT 720,
            map_id INTEGER DEFAULT 3,
            party_name TEXT DEFAULT ''
        );

        -- 0x001D entity settings (16-byte form)
        CREATE TABLE IF NOT EXISTS player_settings (
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

        -- 0x0008 NPCs (65 bytes each)
        CREATE TABLE IF NOT EXISTS npcs (
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

        -- 0x000E Entity spawns (45 bytes each)
        CREATE TABLE IF NOT EXISTS entities (
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

        -- 0x000F Mob spawns (46 bytes each)
        CREATE TABLE IF NOT EXISTS mobs (
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

        -- 0x0005 Position updates (24 bytes each)
        CREATE TABLE IF NOT EXISTS positions (
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

        -- Equipment currently worn by a player. slot_idx maps to the
        -- 11 slots sent in the 0x0001 player-spawn packet (8 at offset
        -- 66-97, plus slots 13/14/15 at offsets 98/102/108).
        CREATE TABLE IF NOT EXISTS equipment (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            slot_idx INTEGER NOT NULL,
            item_id INTEGER NOT NULL DEFAULT 0,
            UNIQUE(entity_id, slot_idx)
        );

        -- Player inventory (bag). item_id 0 = empty slot.
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER NOT NULL,
            slot_idx INTEGER NOT NULL,
            item_id INTEGER NOT NULL DEFAULT 0,
            quantity INTEGER NOT NULL DEFAULT 1,
            UNIQUE(entity_id, slot_idx)
        );
    """)

    # Add player stat columns (idempotent — ignore if already exist)
    for col, default in [
        ('hp', 294), ('hp_max', 294),
        ('mp', 280), ('mp_max', 280),
        ('gold', 500),
        ('experience', 0),
        # Appearance bytes sent by the client's CREATE packet (sub_4C0C50
        # body[1..5]). These feed byte_958C4E..52 on the character-select
        # screen; the same 5 bytes ALSO need to be patched into init_pkt1's
        # 0x0002 profile sub-message once we locate them there.
        ('app0', 0), ('app1', 0), ('app2', 0), ('app3', 0), ('app4', 0),
    ]:
        try:
            conn.execute(f"ALTER TABLE players ADD COLUMN {col} INTEGER DEFAULT {default}")
        except sqlite3.OperationalError:
            pass  # Column already exists

    # Add account_id FK to players (idempotent for existing DBs)
    try:
        conn.execute("ALTER TABLE players ADD COLUMN account_id INTEGER REFERENCES accounts(id)")
    except sqlite3.OperationalError:
        pass

    conn.commit()


# ---------- Account queries ----------

def _hash_password(password: str, salt: str) -> str:
    """Hash a password with SHA-256 and a salt."""
    return hashlib.sha256((salt + password).encode('utf-8')).hexdigest()


def get_account(username: str) -> sqlite3.Row | None:
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM accounts WHERE username = ?", (username,)
    ).fetchone()


def create_account(username: str, password: str) -> sqlite3.Row:
    """Create a new account. Returns the account row."""
    conn = get_connection()
    salt = os.urandom(16).hex()
    pw_hash = _hash_password(password, salt)
    conn.execute(
        "INSERT INTO accounts (username, password_hash, salt) VALUES (?, ?, ?)",
        (username, pw_hash, salt)
    )
    conn.commit()
    return get_account(username)


def verify_password(account: sqlite3.Row, password: str) -> bool:
    """Check a password against an account's stored hash."""
    return _hash_password(password, account['salt']) == account['password_hash']


def get_characters_for_account(account_id: int) -> list[sqlite3.Row]:
    """Get all characters belonging to an account."""
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM players WHERE account_id = ? ORDER BY id", (account_id,)
    ).fetchall()


def create_character(account_id: int, name: str, class_id: int = 0,
                     appearance: tuple[int, int, int, int, int] = (0, 0, 0, 0, 0)
                     ) -> sqlite3.Row:
    """Create a new character for an account. Starts as Novice (class 0) by default.

    `appearance` is the 5-byte appearance block sent by the client's CREATE
    packet (body[1..5]). These bytes drive the 3D character model and the
    character-select icon.

    Returns the new player row.
    """
    conn = get_connection()
    entity_id = random.randint(0x10000000, 0x7FFFFFFF)
    # Ensure unique entity_id
    while get_player(entity_id) is not None:
        entity_id = random.randint(0x10000000, 0x7FFFFFFF)

    conn.execute(
        "INSERT INTO players (account_id, entity_id, name, class_id, "
        "app0, app1, app2, app3, app4) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (account_id, entity_id, name, class_id, *appearance)
    )
    conn.commit()
    seed_equipment(entity_id, class_id)
    return get_player(entity_id)


def update_player_class(entity_id: int, class_id: int):
    """Update a player's class (e.g. after Census Angel selection)."""
    conn = get_connection()
    conn.execute(
        "UPDATE players SET class_id = ? WHERE entity_id = ?",
        (class_id, entity_id)
    )
    conn.commit()


def rename_character(entity_id: int, new_name: str):
    """Rename a player character."""
    conn = get_connection()
    conn.execute(
        "UPDATE players SET name = ? WHERE entity_id = ?",
        (new_name, entity_id)
    )
    conn.commit()


def delete_character(entity_id: int):
    """Delete a player character from the DB."""
    conn = get_connection()
    conn.execute("DELETE FROM players WHERE entity_id = ?", (entity_id,))
    conn.commit()


# ---------- Player queries ----------

def get_player(entity_id: int) -> sqlite3.Row | None:
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM players WHERE entity_id = ?", (entity_id,)
    ).fetchone()


def get_player_by_name(name: str) -> sqlite3.Row | None:
    conn = get_connection()
    return conn.execute(
        "SELECT * FROM players WHERE name = ?", (name,)
    ).fetchone()


def get_or_create_player(entity_id: int, name: str, **kwargs) -> sqlite3.Row:
    conn = get_connection()
    row = get_player(entity_id)
    if row is None:
        cols = ["entity_id", "name"] + list(kwargs.keys())
        vals = [entity_id, name] + list(kwargs.values())
        placeholders = ", ".join("?" for _ in vals)
        col_str = ", ".join(cols)
        conn.execute(f"INSERT INTO players ({col_str}) VALUES ({placeholders})", vals)
        conn.commit()
        row = get_player(entity_id)
    return row


def update_player_position(entity_id: int, x: int, y: int):
    conn = get_connection()
    conn.execute(
        "UPDATE players SET pos_x = ?, pos_y = ? WHERE entity_id = ?",
        (x, y, entity_id)
    )
    conn.commit()


def update_player_map(entity_id: int, map_id: int, x: int, y: int):
    """Update the player's current map and position."""
    conn = get_connection()
    conn.execute(
        "UPDATE players SET map_id = ?, pos_x = ?, pos_y = ? WHERE entity_id = ?",
        (map_id, x, y, entity_id)
    )
    conn.commit()


def update_player_stats(entity_id: int, hp: int, mp: int):
    """Update the player's current HP and MP."""
    conn = get_connection()
    conn.execute(
        "UPDATE players SET hp = ?, mp = ? WHERE entity_id = ?",
        (hp, mp, entity_id)
    )
    conn.commit()


def update_player_full(entity_id: int, **fields):
    """Update arbitrary columns on a player row. Keys must match schema."""
    if not fields:
        return
    conn = get_connection()
    cols = ", ".join(f"{k} = ?" for k in fields)
    conn.execute(
        f"UPDATE players SET {cols} WHERE entity_id = ?",
        (*fields.values(), entity_id),
    )
    conn.commit()


# ---------- Leveling ----------

def xp_for_level(level: int) -> int:
    """XP required to REACH (not advance past) the given level.

    Level 1 requires 0. Curve is mild-quadratic so early mobs matter but
    high levels still feel earned: level N needs ~50 * (N-1)^2 XP.
    """
    if level <= 1:
        return 0
    return 50 * (level - 1) * (level - 1)


def level_for_xp(xp: int) -> int:
    """Max level reachable with the given total XP (capped at 99)."""
    for lvl in range(99, 0, -1):
        if xp >= xp_for_level(lvl):
            return lvl
    return 1


# ---------- Equipment & inventory ----------

# Starter gear keyed by class_id. item_id values are placeholders that
# point at entries in goods.xml once we load it. For now they're just
# non-zero so remote player spawns carry *something* and don't render
# as the all-zero naked template.
#
# Slot map (per 0x0001 spawn packet):
#   0-7  main equipment slots (offsets 66-97)
#   8    slot 13 at offset 98
#   9    slot 14 at offset 102
#   10   slot 15 at offset 108
_STARTER_EQUIPMENT: dict[int, list[int]] = {
    # Novice: cloth shirt+pants, minimal
    0:  [101, 102, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    # Priest: robe + staff
    1:  [110, 111, 0, 0, 112, 0, 0, 0, 0, 0, 0],
    # Summoner: robe + wand
    2:  [120, 121, 0, 0, 122, 0, 0, 0, 0, 0, 0],
    # Wizard: robe + staff
    3:  [130, 131, 0, 0, 132, 0, 0, 0, 0, 0, 0],
    # Magician: robe + rod
    4:  [140, 141, 0, 0, 142, 0, 0, 0, 0, 0, 0],
    # Protector: heavy armor + shield + mace
    5:  [150, 151, 0, 152, 153, 0, 0, 0, 0, 0, 0],
    # Warrior: plate + axe
    6:  [160, 161, 0, 0, 162, 0, 0, 0, 0, 0, 0],
    # Swordsman: plate + sword
    7:  [170, 171, 0, 0, 172, 0, 0, 0, 0, 0, 0],
    # Spearman: plate + spear
    8:  [180, 181, 0, 0, 182, 0, 0, 0, 0, 0, 0],
    # Archer: leather + bow
    9:  [190, 191, 0, 0, 192, 0, 0, 0, 0, 0, 0],
}

EQUIPMENT_SLOT_COUNT = 11
INVENTORY_SLOT_COUNT = 40


def seed_equipment(entity_id: int, class_id: int):
    """Populate equipment slots with starter gear for the given class."""
    conn = get_connection()
    gear = _STARTER_EQUIPMENT.get(class_id, _STARTER_EQUIPMENT[0])
    for slot_idx in range(EQUIPMENT_SLOT_COUNT):
        item_id = gear[slot_idx] if slot_idx < len(gear) else 0
        conn.execute(
            "INSERT OR REPLACE INTO equipment (entity_id, slot_idx, item_id) "
            "VALUES (?, ?, ?)",
            (entity_id, slot_idx, item_id),
        )
    conn.commit()


def get_equipment(entity_id: int) -> list[int]:
    """Return the 11 equipment slot item_ids for a character. Missing rows
    are treated as 0 (empty). Output length is always EQUIPMENT_SLOT_COUNT.
    """
    conn = get_connection()
    rows = conn.execute(
        "SELECT slot_idx, item_id FROM equipment WHERE entity_id = ?",
        (entity_id,),
    ).fetchall()
    out = [0] * EQUIPMENT_SLOT_COUNT
    for r in rows:
        if 0 <= r['slot_idx'] < EQUIPMENT_SLOT_COUNT:
            out[r['slot_idx']] = r['item_id']
    return out


def set_equipment_slot(entity_id: int, slot_idx: int, item_id: int):
    conn = get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO equipment (entity_id, slot_idx, item_id) "
        "VALUES (?, ?, ?)",
        (entity_id, slot_idx, item_id),
    )
    conn.commit()


def get_inventory(entity_id: int) -> list[sqlite3.Row]:
    conn = get_connection()
    return conn.execute(
        "SELECT slot_idx, item_id, quantity FROM inventory "
        "WHERE entity_id = ? ORDER BY slot_idx",
        (entity_id,),
    ).fetchall()


def add_to_inventory(entity_id: int, item_id: int, quantity: int = 1) -> int:
    """Add `quantity` of `item_id` to the first available inventory slot
    (merging with an existing stack of the same item_id if present).

    Returns the slot_idx that was written, or -1 if inventory is full.
    """
    conn = get_connection()
    # Merge with existing stack first
    existing = conn.execute(
        "SELECT slot_idx, quantity FROM inventory "
        "WHERE entity_id = ? AND item_id = ? LIMIT 1",
        (entity_id, item_id),
    ).fetchone()
    if existing is not None:
        conn.execute(
            "UPDATE inventory SET quantity = quantity + ? "
            "WHERE entity_id = ? AND slot_idx = ?",
            (quantity, entity_id, existing['slot_idx']),
        )
        conn.commit()
        return existing['slot_idx']
    # Otherwise find the first empty slot index not in use
    used = {r['slot_idx'] for r in conn.execute(
        "SELECT slot_idx FROM inventory WHERE entity_id = ?", (entity_id,)
    ).fetchall()}
    for slot in range(INVENTORY_SLOT_COUNT):
        if slot not in used:
            conn.execute(
                "INSERT INTO inventory (entity_id, slot_idx, item_id, quantity) "
                "VALUES (?, ?, ?, ?)",
                (entity_id, slot, item_id, quantity),
            )
            conn.commit()
            return slot
    return -1


# ---------- Settings queries ----------

def get_player_settings(player_id: int, packet_num: int = None) -> list[sqlite3.Row]:
    conn = get_connection()
    if packet_num is not None:
        return conn.execute(
            "SELECT * FROM player_settings WHERE player_id = ? AND packet_num = ? "
            "ORDER BY marker, setting_id",
            (player_id, packet_num)
        ).fetchall()
    return conn.execute(
        "SELECT * FROM player_settings WHERE player_id = ? ORDER BY marker, setting_id",
        (player_id,)
    ).fetchall()


# ---------- World data queries ----------

def get_npcs(source: str = None) -> list[sqlite3.Row]:
    conn = get_connection()
    if source:
        return conn.execute(
            "SELECT * FROM npcs WHERE source = ? ORDER BY id", (source,)
        ).fetchall()
    return conn.execute("SELECT * FROM npcs ORDER BY id").fetchall()


def get_entities(source: str = None) -> list[sqlite3.Row]:
    conn = get_connection()
    if source:
        return conn.execute(
            "SELECT * FROM entities WHERE source = ? ORDER BY id", (source,)
        ).fetchall()
    return conn.execute("SELECT * FROM entities ORDER BY id").fetchall()


def get_mobs(source: str = None) -> list[sqlite3.Row]:
    conn = get_connection()
    if source:
        return conn.execute(
            "SELECT * FROM mobs WHERE source = ? ORDER BY id", (source,)
        ).fetchall()
    return conn.execute("SELECT * FROM mobs ORDER BY id").fetchall()


def get_positions(packet_num: int = None, source: str = None) -> list[sqlite3.Row]:
    conn = get_connection()
    conditions = []
    params = []
    if packet_num is not None:
        conditions.append("packet_num = ?")
        params.append(packet_num)
    if source:
        conditions.append("source = ?")
        params.append(source)
    where = " AND ".join(conditions) if conditions else "1=1"
    return conn.execute(
        f"SELECT * FROM positions WHERE {where} ORDER BY id", params
    ).fetchall()


def get_area_data(area_packet_num: int) -> list[sqlite3.Row]:
    """Get all area sub-messages for a specific area packet, ordered correctly.

    Returns rows from npcs, entities, mobs, and positions
    that belong to this area packet, sorted by msg_order.
    """
    conn = get_connection()
    query = """
        SELECT msg_order, 'npc' as type, id as ref_id FROM npcs
            WHERE source='area' AND area_packet_num=?
        UNION ALL
        SELECT msg_order, 'entity' as type, id as ref_id FROM entities
            WHERE source='area' AND area_packet_num=?
        UNION ALL
        SELECT msg_order, 'mob' as type, id as ref_id FROM mobs
            WHERE source='area' AND area_packet_num=?
        UNION ALL
        SELECT msg_order, 'position' as type, id as ref_id FROM positions
            WHERE source='area' AND area_packet_num=?
        ORDER BY msg_order
    """
    params = [area_packet_num] * 4
    return conn.execute(query, params).fetchall()


def get_area_packet_count() -> int:
    """Get the number of distinct area packets in the database."""
    conn = get_connection()
    row = conn.execute("""
        SELECT MAX(area_packet_num) FROM (
            SELECT area_packet_num FROM npcs WHERE source='area'
            UNION SELECT area_packet_num FROM entities WHERE source='area'
            UNION SELECT area_packet_num FROM mobs WHERE source='area'
            UNION SELECT area_packet_num FROM positions WHERE source='area'
        )
    """).fetchone()
    return row[0] or 0


# ---------- Init ----------

def init():
    """Initialize the database (creates tables if needed)."""
    get_connection()

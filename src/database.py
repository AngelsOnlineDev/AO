"""SQLite database for Angels Online private server.

Stores player data, NPC definitions, entity spawns, and positions.

Tables use a 'source' column to distinguish init-phase data from
area-phase data. Area data also tracks which area packet and
sub-message order it came from.
"""

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
        -- Player characters
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY,
            entity_id INTEGER UNIQUE NOT NULL,
            name TEXT NOT NULL,
            level INTEGER DEFAULT 1,
            class_id INTEGER DEFAULT 0,
            pos_x INTEGER DEFAULT 1040,
            pos_y INTEGER DEFAULT 720,
            map_id INTEGER DEFAULT 0,
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
    """)
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

"""Server configuration for Angels Online private server.

All configurable settings in one place. Override with environment variables:
    AO_HOST=0.0.0.0            - Listen address (default: 127.0.0.1)
    AO_LOGIN_PORT=16768        - Login server port
    AO_WORLD_PORT=27901        - World server port
    AO_FILE_PORT=21238         - File server port
    AO_GAME_DIR=C:\...         - Angels Online install directory
"""

import os

# Network
HOST = os.environ.get("AO_HOST", "127.0.0.1")
# Address sent in redirect packet (what the client connects to for world server).
# Defaults to HOST, but set separately if listening on 0.0.0.0.
REDIRECT_HOST = os.environ.get("AO_REDIRECT_HOST", HOST)
LOGIN_PORT = int(os.environ.get("AO_LOGIN_PORT", "16768"))
WORLD_PORT = int(os.environ.get("AO_WORLD_PORT", "27901"))
FILE_PORT = int(os.environ.get("AO_FILE_PORT", "21238"))

# Game
KEEPALIVE_INTERVAL = 1.0    # seconds between keepalive packets (real server: ~1-2s)
MOVE_SPEED = 110            # character movement speed (real server: 0x6E for players)
MAX_MOVE_STEP = 200         # max pixels per movement segment
START_MAP_ID = 2            # default starting map (map002.mpc = Angel Lyceum / Eden)
DEFAULT_SPAWN = (1040, 720) # fallback spawn position for maps without a defined point

# Known spawn positions per map — (x, y) in tile-pixel coordinates.
# Action type 37 params are (map_id, spawn_point, flag).  spawn_point selects
# among multiple positions on the same map (0, 1, 2).  We key by (map_id, point).
# Falls back to (map_id, 0), then DEFAULT_SPAWN.
MAP_SPAWN_POINTS: dict[tuple[int, int], tuple[int, int]] = {
    # Eden / Angel Lyceum (starting zone)
    (2, 0):   (1040, 720),
    # Neighboring maps reachable from Eden via gate NPCs or dialog warps
    (3, 0):   (500, 500),
    (52, 0):  (500, 500),
    (63, 0):  (500, 500),
    (63, 1):  (800, 500),
    (63, 2):  (500, 800),
    (64, 0):  (500, 500),
    (65, 0):  (500, 500),
    (66, 0):  (500, 500),
    (66, 1):  (800, 500),
    (66, 2):  (500, 800),
    (81, 0):  (500, 500),
}

# Paths
import pathlib

# Running as a script: data lives in the server/ directory (parent of src/)
_BASE_DIR = pathlib.Path(__file__).parent.parent

# Extracted game XML files (msg.xml, spmsg.xml, npc.xml, etc.)
GAME_XML_DIR = _BASE_DIR / "data" / "game_xml"

# Angels Online game install dir (contains *.pak files for map loading).
# game_finder.ensure_game_dir() will update this at startup if needed.
# Override with AO_GAME_DIR environment variable if installed elsewhere.
GAME_DIR = pathlib.Path(
    os.environ.get("AO_GAME_DIR",
                   r"C:\Program Files (x86)\Angels Online")
)

# Login MOTD (displayed in client login screen)
LOGIN_MOTD = (
    r"\n Welcome to Angels Online Private Server!\n"
    r"\n This is a fan-operated server started by SquirrelMan of RageZone."
    r"\n\n Enjoy your stay in the lands of Eden.\n"
)

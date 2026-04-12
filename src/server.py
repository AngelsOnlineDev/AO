"""
Angels Online Private Server - Main Entry Point
=================================================
Runs the Login Server, World Server, and File Server.

Usage:
  python server.py

Configuration via environment variables (see config.py):
  AO_HOST=0.0.0.0  AO_LOGIN_PORT=16768  python server.py
"""

import asyncio
import logging
import sys
from pathlib import Path

from game_server import LoginServer
from world_server import WorldServer
from file_server import FileServer
import patch_server
import ftp_server
import config
import database
import game_finder

# Logs go to server/logs/ directory (not cluttering src/)
LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / "server.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("main")


async def main():
    # Resolve the game install directory (checks env, saved config, registry,
    # common paths, and optionally prompts with a GUI folder dialog).
    game_dir = game_finder.ensure_game_dir(allow_gui=True)
    config.GAME_DIR = game_dir

    login_server = LoginServer(
        host=config.HOST,
        port=config.LOGIN_PORT,
        world_host=config.REDIRECT_HOST,
        world_port=config.WORLD_PORT,
    )

    world_server = WorldServer(
        host=config.HOST,
        port=config.WORLD_PORT,
    )

    file_server = FileServer(config.HOST, config.FILE_PORT)

    # Initialize database
    database.init()
    player_count = database.get_connection().execute(
        "SELECT COUNT(*) FROM players").fetchone()[0]
    if player_count == 0:
        log.warning("No players in database! Run: python tools/seed_database.py")

    log.info("=" * 60)
    log.info("Angels Online Private Server")
    log.info("=" * 60)
    log.info(f"Database: {database.DB_PATH} ({player_count} players)")
    log.info(f"Login Server:  {config.HOST}:{config.LOGIN_PORT}")
    log.info(f"World Server:  {config.HOST}:{config.WORLD_PORT}")
    log.info(f"File Server:   {config.HOST}:{config.FILE_PORT}")
    log.info(f"Patch Server:  {config.HOST}:80 (needs admin — launches Start.exe)")
    log.info(f"FTP Server:    {config.HOST}:21 (needs admin — Start.exe FTP check)")
    log.info("=" * 60)
    log.info("CLIENT SETUP: Set SERVER.XML ip/fip to %s", config.HOST)
    log.info("START.EXE SETUP: Add to hosts file (as admin):")
    log.info("  127.0.0.1 ao.igg.com")
    log.info("  127.0.0.1 aoupdate1.iggcn.com")
    log.info("Waiting for client connections...")

    await asyncio.gather(
        login_server.start(),
        world_server.start(),
        file_server.start(),
        patch_server.start(config.HOST, 80),
        ftp_server.start(config.HOST, 21),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.info("Server shutting down...")

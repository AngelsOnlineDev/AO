"""Fake FTP server for Angels Online Start.exe using pyftpdlib.

Start.exe connects to aoupdate1.iggcn.com via FTP (anonymous login) to
check for patch files. We spin up a real FTP server serving a temporary
directory containing fake "no updates" INI files.

Uses pyftpdlib which is battle-tested against MFC's CFtpFileFind.
Port 21 requires administrator on Windows.
"""

import asyncio
import logging
import os
import tempfile
import threading
from pathlib import Path

log = logging.getLogger("ftp_server")

# Update INI content — tells the launcher the current version is the latest
UPDATE_INI_CONTENT = b"""[INFO]
GameVer=8.5.0.3
GameVerFrom=8.5.0.3
GameVerTrans=8.5.0.3
LoaderVer=1.7
LoaderURL=
GameURL=
Server=
"""

# Files to create in the FTP root. Start.exe seems to look for version.ini
# (treats it as both a file AND probes directory-style access).
VIRTUAL_FILES = {
    "version.ini": UPDATE_INI_CONTENT,
    "update.ini": UPDATE_INI_CONTENT,
    "update3.ini": UPDATE_INI_CONTENT,
    "UPDATER.INI": UPDATE_INI_CONTENT,
}


def _prepare_ftp_root() -> Path:
    """Create a temp directory with the virtual files."""
    root = Path(tempfile.gettempdir()) / "ao_fake_ftp"
    root.mkdir(exist_ok=True)
    for fname, content in VIRTUAL_FILES.items():
        (root / fname).write_bytes(content)
    return root


def _run_server_blocking(host: str, port: int, root: Path):
    """Blocking pyftpdlib server entry point (run in a thread)."""
    from pyftpdlib.authorizers import DummyAuthorizer
    from pyftpdlib.handlers import FTPHandler
    from pyftpdlib.servers import ThreadedFTPServer

    # Enable DEBUG logging so we can see exactly what Start.exe sends
    py_log = logging.getLogger("pyftpdlib")
    py_log.setLevel(logging.DEBUG)

    authorizer = DummyAuthorizer()
    authorizer.add_anonymous(str(root))

    handler = FTPHandler
    handler.authorizer = authorizer
    handler.banner = "AO Fake FTP ready."
    handler.passive_ports = range(60000, 60100)
    # Tell passive mode to advertise 127.0.0.1 to the client
    handler.masquerade_address = "127.0.0.1"

    try:
        # ThreadedFTPServer uses its own thread pool, not asyncio
        server = ThreadedFTPServer((host, port), handler)
    except PermissionError:
        log.error(f"FTP server: port {port} requires administrator.")
        return
    except OSError as e:
        log.error(f"FTP server failed to bind {host}:{port}: {e}")
        return

    log.info(f"FTP server listening on {host}:{port} (root={root})")
    try:
        server.serve_forever()
    except Exception as e:
        log.error(f"FTP server crashed: {e}")
    finally:
        server.close_all()


async def start(host: str = "0.0.0.0", port: int = 21):
    """Start the FTP server in a background thread (pyftpdlib is blocking)."""
    root = _prepare_ftp_root()
    thread = threading.Thread(
        target=_run_server_blocking,
        args=(host, port, root),
        daemon=True,
        name="ftp_server",
    )
    thread.start()
    # Return a future that never completes so asyncio.gather() treats this
    # like the other async servers
    await asyncio.Event().wait()

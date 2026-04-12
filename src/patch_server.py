"""Fake patch/update HTTP server for Angels Online Start.exe.

Start.exe fetches:
  http://ao.igg.com/patch.php              — patch info
  http://ao.igg.com/download/downloadpatch.php  — patch downloads
  http://ao.igg.com/<relative>.ini         — version/loader info
  http://aoupdate1.iggcn.com/<paths>       — alternate update host

To redirect these to our fake server, add to C:\\Windows\\System32\\drivers\\etc\\hosts:
  127.0.0.1  ao.igg.com
  127.0.0.1  aoupdate1.iggcn.com

Then run this server as administrator on port 80. Start.exe will think the
game is already up to date and proceed to launch it.
"""

import asyncio
import logging

log = logging.getLogger("patch_server")

# An update.ini response that reports the same version the client has, so
# Start.exe decides no update is needed. Section header [INFO] matches the
# key prefixes Start.exe looks for (INFO\GameVer, INFO\LoaderVer, etc.).
UPDATE_INI = b"""[INFO]
GameVer = 8.5.0.3
GameVerFrom = 8.5.0.3
GameVerTrans = 8.5.0.3
LoaderVer = 1.7
LoaderURL =
GameURL =
Server =
"""

# HTML response for patch.php — Start.exe's embedded IE browser shows this
# as the "patch notes" page. Plain text causes IE to offer saving the file.
PATCH_HTML = b"""<!DOCTYPE html>
<html>
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
  <title>Angels Online Patch Notes</title>
  <style>
    body { font-family: sans-serif; background: #1a1a2e; color: #eee;
           margin: 20px; }
    h1 { color: #7af; }
  </style>
</head>
<body>
  <h1>Angels Online Private Server</h1>
  <p>Running version 8.5.0.3 &mdash; no updates available.</p>
  <p>You are connected to a local development server.</p>
</body>
</html>
"""

HOMEPAGE_HTML = PATCH_HTML  # Reuse same HTML for the HOME PAGE button too


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
    addr = writer.get_extra_info("peername")
    try:
        # Read HTTP request
        request_line = await reader.readline()
        if not request_line:
            return
        parts = request_line.decode("ascii", errors="replace").split()
        if len(parts) < 2:
            return
        method, path = parts[0], parts[1]

        # Read headers until empty line
        while True:
            line = await reader.readline()
            if not line or line == b"\r\n":
                break

        log.info(f"[{addr}] {method} {path}")

        # Route
        lower = path.lower()
        if "patch.php" in lower:
            # Embedded IE browser shows this — must be HTML or it offers save
            body = PATCH_HTML
            status = b"200 OK"
            ctype = b"text/html; charset=utf-8"
        elif lower.endswith(".ini") or "update" in lower:
            body = UPDATE_INI
            status = b"200 OK"
            ctype = b"text/plain"
        elif "downloadpatch" in lower:
            body = b""
            status = b"200 OK"
            ctype = b"application/octet-stream"
        elif lower in ("/", "/index.html", "/index.htm"):
            body = HOMEPAGE_HTML
            status = b"200 OK"
            ctype = b"text/html; charset=utf-8"
        else:
            body = HOMEPAGE_HTML
            status = b"200 OK"
            ctype = b"text/html; charset=utf-8"

        resp = (
            b"HTTP/1.1 " + status + b"\r\n"
            b"Content-Type: " + ctype + b"\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"Connection: close\r\n"
            b"\r\n"
        ) + body
        writer.write(resp)
        await writer.drain()
    except (ConnectionResetError, BrokenPipeError):
        pass
    except Exception as e:
        log.warning(f"[{addr}] Error: {e}")
    finally:
        if not writer.is_closing():
            writer.close()


async def start(host: str = "0.0.0.0", port: int = 80):
    """Start the fake patch HTTP server.

    Port 80 requires administrator privileges on Windows. Run the whole
    server process elevated, or use netsh portproxy to forward 80 → 8080.
    """
    try:
        server = await asyncio.start_server(_handle_client, host, port)
    except PermissionError:
        log.error(f"Patch server: port {port} requires administrator. "
                  f"Either run as admin or set PATCH_SERVER_PORT to a "
                  f"non-privileged port (e.g. 8080) and use netsh portproxy.")
        return
    except OSError as e:
        log.error(f"Patch server failed to bind {host}:{port}: {e}")
        return
    log.info(f"Patch server listening on {host}:{port}")
    log.info("Add to hosts file: 127.0.0.1 ao.igg.com aoupdate1.iggcn.com")
    async with server:
        await server.serve_forever()

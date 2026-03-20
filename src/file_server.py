"""
Angels Online File Server
===========================
Handles file/asset requests on port 21238 (fip/fport from SERVER.XML).
This likely serves game data files or handles subset/channel switching.
"""

import asyncio
import logging

log = logging.getLogger("file_server")


class FileServer:
    """File server - handles the fip/fport connection."""

    def __init__(self, host: str, port: int):
        self.host = host
        self.port = port

    async def start(self):
        server = await asyncio.start_server(
            self._handle_client, self.host, self.port
        )
        log.info(f"File server listening on {self.host}:{self.port}")
        async with server:
            await server.serve_forever()

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        addr = writer.get_extra_info("peername")
        log.info(f"File server: Connection from {addr}")

        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                log.debug(f"File server recv {len(data)} bytes: {data[:64].hex(' ')}")
                # TODO: Implement file server protocol after capture analysis
        except (ConnectionResetError, BrokenPipeError):
            pass
        finally:
            writer.close()
            log.info(f"File server: {addr} disconnected")

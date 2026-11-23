from __future__ import annotations

import asyncio
import logging
import os
import sys
from urllib.parse import urlsplit

import websockets
from dotenv import load_dotenv

load_dotenv()

LOGGER = logging.getLogger("stackchan.xiaozhi_mcp")
INITIAL_BACKOFF_SECONDS = 1
MAX_BACKOFF_SECONDS = 60


def validate_endpoint(endpoint: str) -> str:
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"ws", "wss"} or not parsed.netloc:
        raise ValueError("XIAOZHI_MCP_ENDPOINT must be a valid ws:// or wss:// URL")
    if not parsed.query:
        raise ValueError("XIAOZHI_MCP_ENDPOINT is missing its token query string")
    return parsed.hostname or "unknown-host"


async def _pipe_websocket_to_stdio(websocket, process: asyncio.subprocess.Process) -> None:
    assert process.stdin is not None
    async for message in websocket:
        if isinstance(message, bytes):
            message = message.decode("utf-8")
        process.stdin.write(message.rstrip("\n").encode("utf-8") + b"\n")
        await process.stdin.drain()


async def _pipe_stdio_to_websocket(process: asyncio.subprocess.Process, websocket) -> None:
    assert process.stdout is not None
    while line := await process.stdout.readline():
        await websocket.send(line.decode("utf-8").rstrip("\n"))


async def _pipe_stderr(process: asyncio.subprocess.Process) -> None:
    assert process.stderr is not None
    while line := await process.stderr.readline():
        LOGGER.info("MCP server: %s", line.decode("utf-8", errors="replace").rstrip())


async def connect_once(endpoint: str) -> None:
    host = validate_endpoint(endpoint)
    LOGGER.info("Connecting to XiaoZhi MCP host %s", host)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "stackchan_gateway.mcp_tools",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "STACKCHAN_MCP_SHARED_TIMERS": "1"},
    )
    try:
        async with websockets.connect(endpoint, ping_interval=20, ping_timeout=20) as websocket:
            LOGGER.info("Connected; advertising configured StackChan tools")
            tasks = [
                asyncio.create_task(_pipe_websocket_to_stdio(websocket, process)),
                asyncio.create_task(_pipe_stdio_to_websocket(process, websocket)),
                asyncio.create_task(_pipe_stderr(process)),
            ]
            done, pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            for task in done:
                task.result()
    finally:
        if process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except TimeoutError:
                process.kill()
                await process.wait()


async def run_forever(endpoint: str) -> None:
    backoff = INITIAL_BACKOFF_SECONDS
    while True:
        try:
            await connect_once(endpoint)
            backoff = INITIAL_BACKOFF_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            LOGGER.warning(
                "MCP connection ended (%s); retrying in %ss",
                type(exc).__name__,
                backoff,
            )
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    endpoint = os.getenv("XIAOZHI_MCP_ENDPOINT", "").strip()
    if not endpoint:
        raise SystemExit(
            "XIAOZHI_MCP_ENDPOINT is not configured. Copy it from StackChan World > Settings > MCP."
        )
    try:
        asyncio.run(run_forever(endpoint))
    except KeyboardInterrupt:
        LOGGER.info("Stopped")


if __name__ == "__main__":
    main()

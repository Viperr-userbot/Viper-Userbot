"""Telethon client bootstrap + graceful shutdown."""
import asyncio
import logging
import signal
import time

import config
from telethon import TelegramClient

log = logging.getLogger("hwh")

_shutdown_event = asyncio.Event()
_client = None
_start_monotonic = 0.0


def make_client():
    global _client
    if _client is not None:
        return _client
    session_data = config.get_telethon_session()
    _client = TelegramClient(
        session_data,
        config.SANGMATA_API_ID,
        config.SANGMATA_API_HASH,
        connection_retries=None,
        retry_delay=2,
        auto_reconnect=True,
        flood_sleep_threshold=120,
        request_retries=10,
    )
    return _client


def client():
    if _client is None:
        raise RuntimeError("client not started -- run main()")
    return _client


async def start_client():
    global _start_monotonic
    c = make_client()
    await c.start()
    _start_monotonic = time.monotonic()
    log.info("Telegram client started after %.1fs", _start_monotonic)
    return c


async def stop_client():
    global _start_monotonic
    if _client is None:
        return
    await _client.disconnect()
    elapsed = time.monotonic() - _start_monotonic
    _start_monotonic = 0.0
    log.info("Telegram client disconnected after %.1fs online", elapsed)


def shutdown_requested() -> bool:
    return _shutdown_event.is_set()


def request_shutdown() -> None:
    _shutdown_event.set()


def register_signals():
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, request_shutdown)
        except NotImplementedError:
            pass


def get_uptime_seconds() -> float:
    return time.monotonic() - _start_monotonic if _start_monotonic else 0.0

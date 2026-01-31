from __future__ import annotations

import asyncio
import json
import os
from typing import Any

import websockets
from channels.generic.websocket import AsyncWebsocketConsumer
from django.conf import settings
from websockets.exceptions import ConnectionClosed


class WarStreamConsumer(AsyncWebsocketConsumer):
    """
    Simple proxy WebSocket:
      browser <-> our server <-> Twelve Data WS

    Keeps TWELVE_DATA_API_KEY server-side and forwards price events to the client.
    """

    upstream = None
    upstream_reader_task: asyncio.Task | None = None
    upstream_heartbeat_task: asyncio.Task | None = None

    async def connect(self):
        await self.accept()

        # Optional: require login for access. In local/dev, allow anonymous access
        # so the page works out-of-the-box.
        user = getattr(self.scope, "user", None)
        if (not user or not user.is_authenticated) and not getattr(settings, "DEBUG", False):
            await self.send_json({"type": "error", "error": "NOT_AUTHENTICATED"})
            await self.close(code=4401)
            return

        # In this project, `.env` is loaded by Django settings at startup.
        # But the consumer runs in ASGI context; fall back to Django settings if needed.
        api_key = os.environ.get("TWELVE_DATA_API_KEY") or getattr(settings, "TWELVE_DATA_API_KEY", None)
        if not api_key:
            await self.send_json({"type": "error", "error": "MISSING_TWELVE_DATA_API_KEY"})
            await self.close(code=1011)
            return

        # Connect upstream immediately so subscribe is fast.
        url = f"wss://ws.twelvedata.com/v1/quotes/price?apikey={api_key}"
        try:
            self.upstream = await websockets.connect(url)
        except Exception:
            await self.send_json({"type": "error", "error": "UPSTREAM_CONNECT_FAILED"})
            await self.close(code=1011)
            return

        self.upstream_reader_task = asyncio.create_task(self._read_upstream_loop())
        self.upstream_heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        await self.send_json({"type": "ready"})

    async def disconnect(self, code):
        for t in (self.upstream_reader_task, self.upstream_heartbeat_task):
            if t:
                t.cancel()
        if self.upstream:
            try:
                await self.upstream.close()
            except Exception:
                pass
        self.upstream = None

    async def receive(self, text_data=None, bytes_data=None):
        if not self.upstream:
            return
        if not text_data:
            return
        try:
            msg = json.loads(text_data)
        except Exception:
            return

        action = (msg.get("action") or "").strip()
        symbols = (msg.get("symbols") or "").strip()

        if action == "reset":
            await self._upstream_send({"action": "reset"})
            return

        if action == "subscribe":
            # Reset then subscribe for simplicity.
            await self._upstream_send({"action": "reset"})
            await self._upstream_send({"action": "subscribe", "params": {"symbols": symbols}})
            return

        if action == "unsubscribe":
            await self._upstream_send({"action": "unsubscribe", "params": {"symbols": symbols}})
            return

    async def send_json(self, payload: dict[str, Any]):
        await self.send(text_data=json.dumps(payload))

    async def _upstream_send(self, payload: dict[str, Any]):
        try:
            await self.upstream.send(json.dumps(payload))
        except Exception:
            await self.send_json({"type": "error", "error": "UPSTREAM_SEND_FAILED"})

    async def _heartbeat_loop(self):
        # Twelve Data recommends heartbeats every ~10s.
        while True:
            await asyncio.sleep(10)
            if not self.upstream:
                return
            try:
                await self.upstream.send(json.dumps({"action": "heartbeat"}))
            except Exception:
                return

    async def _read_upstream_loop(self):
        try:
            async for raw in self.upstream:
                try:
                    data = json.loads(raw)
                except Exception:
                    continue
                # Forward upstream events as-is; the client will interpret "price" events.
                await self.send_json({"type": "upstream", "data": data})
        except ConnectionClosed as e:
            await self.send_json(
                {"type": "error", "error": "UPSTREAM_CLOSED", "code": getattr(e, "code", None), "reason": getattr(e, "reason", "")}
            )
            await self.close(code=1011)
        except Exception:
            await self.send_json({"type": "error", "error": "UPSTREAM_DISCONNECTED"})
            await self.close(code=1011)


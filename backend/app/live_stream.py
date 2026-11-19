import asyncio
import json

from fastapi import WebSocket


class LiveStreamService:
    """Relay transient per-device camera frames to authenticated viewers."""

    def __init__(self) -> None:
        self._viewers: dict[int, dict[WebSocket, asyncio.Lock]] = {}
        self._source_counts: dict[tuple[int, int], int] = {}
        self._guard = asyncio.Lock()

    async def add_viewer(self, owner_id: int, socket: WebSocket) -> None:
        async with self._guard:
            self._viewers.setdefault(owner_id, {})[socket] = asyncio.Lock()
            online = [device_id for (source_owner, device_id), count in self._source_counts.items()
                      if source_owner == owner_id and count > 0]
        await socket.send_text(json.dumps({"type": "status_batch", "online_device_ids": online}))

    async def remove_viewer(self, owner_id: int, socket: WebSocket) -> None:
        async with self._guard:
            viewers = self._viewers.get(owner_id)
            if viewers:
                viewers.pop(socket, None)
                if not viewers:
                    self._viewers.pop(owner_id, None)

    async def source_connected(self, owner_id: int, device_id: int) -> None:
        key = (owner_id, device_id)
        async with self._guard:
            self._source_counts[key] = self._source_counts.get(key, 0) + 1
        await self._broadcast(owner_id, json.dumps({
            "type": "status", "device_id": device_id, "online": True
        }))

    async def source_disconnected(self, owner_id: int, device_id: int) -> None:
        key = (owner_id, device_id)
        async with self._guard:
            remaining = max(0, self._source_counts.get(key, 1) - 1)
            if remaining:
                self._source_counts[key] = remaining
            else:
                self._source_counts.pop(key, None)
        if not remaining:
            await self._broadcast(owner_id, json.dumps({
                "type": "status", "device_id": device_id, "online": False
            }))

    async def online_devices(self, owner_id: int) -> set[int]:
        async with self._guard:
            return {device_id for (source_owner, device_id), count in self._source_counts.items()
                    if source_owner == owner_id and count > 0}

    async def publish_frame(self, owner_id: int, device_id: int, data_url: str) -> None:
        await self._broadcast(owner_id, json.dumps({
            "type": "frame", "device_id": device_id, "data_url": data_url
        }), drop_if_busy=True)

    async def publish_detections(self, owner_id: int, device_id: int, detections: list[dict]) -> None:
        await self._broadcast(owner_id, json.dumps({
            "type": "detections", "device_id": device_id, "detections": detections
        }))

    async def _broadcast(self, owner_id: int, message: str, drop_if_busy: bool = False) -> None:
        async with self._guard:
            targets = list(self._viewers.get(owner_id, {}).items())
        failed: list[WebSocket] = []

        async def send(socket: WebSocket, lock: asyncio.Lock) -> None:
            if drop_if_busy and lock.locked():
                return
            try:
                async with lock:
                    await socket.send_text(message)
            except Exception:
                failed.append(socket)

        await asyncio.gather(*(send(socket, lock) for socket, lock in targets))
        for socket in failed:
            await self.remove_viewer(owner_id, socket)


live_stream_service = LiveStreamService()

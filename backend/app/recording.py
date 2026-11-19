from __future__ import annotations

import base64
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from .config import get_settings


@dataclass
class CompletedRecording:
    owner_id: int
    device_id: int
    file_path: str
    started_at: datetime
    ended_at: datetime
    frame_count: int


@dataclass
class ActiveRecording:
    writer: object
    file_path: Path
    started_at: datetime
    started_monotonic: float
    frame_count: int
    width: int
    height: int


class RecordingService:
    """Write independent per-device MJPEG segments from incoming source frames."""

    def __init__(self) -> None:
        self._enabled: set[tuple[int, int]] = set()
        self._active: dict[tuple[int, int], ActiveRecording] = {}
        self._lock = threading.RLock()

    def set_enabled(self, owner_id: int, device_id: int, enabled: bool) -> list[CompletedRecording]:
        key = (owner_id, device_id)
        with self._lock:
            if enabled:
                self._enabled.add(key)
                return []
            self._enabled.discard(key)
            completed = self._finish_locked(key)
            return [completed] if completed else []

    def source_disconnected(self, owner_id: int, device_id: int) -> list[CompletedRecording]:
        with self._lock:
            completed = self._finish_locked((owner_id, device_id))
            return [completed] if completed else []

    def accept_frame(self, owner_id: int, device_id: int, data_url: str) -> list[CompletedRecording]:
        key = (owner_id, device_id)
        with self._lock:
            if key not in self._enabled:
                return []
        try:
            encoded = data_url.split(",", 1)[1]
            image = cv2.imdecode(np.frombuffer(base64.b64decode(encoded), dtype=np.uint8), cv2.IMREAD_COLOR)
        except (IndexError, ValueError):
            return []
        if image is None:
            return []

        settings = get_settings()
        completed: list[CompletedRecording] = []
        with self._lock:
            if key not in self._enabled:
                return completed
            current = self._active.get(key)
            if current and time.monotonic() - current.started_monotonic >= settings.recording_segment_seconds:
                result = self._finish_locked(key)
                if result:
                    completed.append(result)
                current = None
            if current is None:
                current = self._start_locked(owner_id, device_id, image)
                if current is None:
                    return completed
            if image.shape[1] != current.width or image.shape[0] != current.height:
                image = cv2.resize(image, (current.width, current.height))
            current.writer.write(image)
            current.frame_count += 1
        return completed

    def _start_locked(self, owner_id: int, device_id: int, image: np.ndarray) -> ActiveRecording | None:
        settings = get_settings()
        folder = settings.storage_dir / str(owner_id) / "recordings" / str(device_id)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{datetime.now(timezone.utc):%Y%m%dT%H%M%S}-{uuid.uuid4().hex[:8]}.avi"
        height, width = image.shape[:2]
        writer = cv2.VideoWriter(
            str(path), cv2.VideoWriter_fourcc(*"MJPG"), settings.recording_fps, (width, height)
        )
        if not writer.isOpened():
            writer.release()
            path.unlink(missing_ok=True)
            return None
        current = ActiveRecording(
            writer=writer,
            file_path=path,
            started_at=datetime.now(timezone.utc),
            started_monotonic=time.monotonic(),
            frame_count=0,
            width=width,
            height=height,
        )
        self._active[(owner_id, device_id)] = current
        return current

    def _finish_locked(self, key: tuple[int, int]) -> CompletedRecording | None:
        current = self._active.pop(key, None)
        if current is None:
            return None
        current.writer.release()
        if current.frame_count == 0:
            current.file_path.unlink(missing_ok=True)
            return None
        return CompletedRecording(
            owner_id=key[0],
            device_id=key[1],
            file_path=str(current.file_path),
            started_at=current.started_at,
            ended_at=datetime.now(timezone.utc),
            frame_count=current.frame_count,
        )


recording_service = RecordingService()

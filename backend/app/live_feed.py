import base64
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass
class LiveFrame:
    data_url: str
    detections: list[dict]
    captured_at: datetime
    session_id: str
    device_id: int
    updated_monotonic: float


class LiveFeedService:
    """Keep one transient recognition frame per device."""

    def __init__(self) -> None:
        self._frames: dict[tuple[int, int], LiveFrame] = {}
        self._lock = threading.Lock()

    def publish(self, owner_id: int, device_id: int, session_id: str, image: bytes,
                detections: list[dict] | None = None) -> None:
        encoded = base64.b64encode(image).decode("ascii")
        frame = LiveFrame(
            data_url=f"data:image/jpeg;base64,{encoded}",
            detections=detections or [],
            captured_at=datetime.now(timezone.utc),
            session_id=session_id,
            device_id=device_id,
            updated_monotonic=time.monotonic(),
        )
        with self._lock:
            self._frames[(owner_id, device_id)] = frame

    def update_detections(self, owner_id: int, device_id: int, session_id: str,
                          detections: list[dict]) -> None:
        with self._lock:
            current = self._frames.get((owner_id, device_id))
            if current and current.session_id == session_id:
                current.detections = detections

    def latest(self, owner_id: int, device_id: int | None = None) -> tuple[LiveFrame | None, bool]:
        with self._lock:
            if device_id is not None:
                frame = self._frames.get((owner_id, device_id))
            else:
                choices = [frame for (source_owner, _), frame in self._frames.items()
                           if source_owner == owner_id]
                frame = max(choices, key=lambda item: item.updated_monotonic) if choices else None
            if not frame:
                return None, False
            return frame, time.monotonic() - frame.updated_monotonic <= 3.0


live_feed_service = LiveFeedService()

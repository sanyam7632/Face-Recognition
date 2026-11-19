import json
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def calibrated_match_threshold() -> float:
    path = Path(__file__).with_name("model_calibration.json")
    try:
        value = float(json.loads(path.read_text(encoding="utf-8"))["cosine_threshold"])
        if -1.0 < value < 1.0:
            return value
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError):
        pass
    return 0.363


class Settings(BaseSettings):
    app_name: str = "Face Recognition API"
    secret_key: str = "development-only-change-me"
    database_url: str = "sqlite:///./data/facelock.db"
    storage_dir: Path = Path("./data/uploads")
    access_token_minutes: int = 1440
    face_match_threshold: float = calibrated_match_threshold()
    unknown_match_threshold: float = 0.36
    face_match_margin: float = 0.04
    face_track_threshold: float = 0.30
    face_confirmation_frames: int = 2
    face_track_expiry_seconds: float = 3.0
    activity_cooldown_seconds: float = 30.0
    recording_fps: float = 15.0
    recording_segment_seconds: int = 600
    unknown_hold_seconds: float = 2.0
    allowed_origins: str = "http://localhost:8081,http://localhost:19006"
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def origins(self) -> list[str]:
        return [x.strip() for x in self.allowed_origins.split(",") if x.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


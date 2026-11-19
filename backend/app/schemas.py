from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterIn(BaseModel):
    email: EmailStr
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: EmailStr
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: EmailStr
    display_name: str
    alerts_enabled: bool


class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class ActivityOut(BaseModel):
    id: int
    label: str
    confidence: float
    seen_at: datetime
    image_url: str | None = None
    device_id: int | None = None
    device_name: str | None = None


class PersonOut(BaseModel):
    id: int
    name: str
    notes: str | None
    face_count: int
    created_at: datetime
    appearances: int = 0
    last_seen: datetime | None = None
    recent_activities: list[ActivityOut] = Field(default_factory=list)


class UnknownOut(BaseModel):
    id: int
    label: str
    first_seen: datetime
    last_seen: datetime
    sightings: int
    image_url: str
    activities: list[ActivityOut] = Field(default_factory=list)


class EventOut(BaseModel):
    id: int
    label: str
    confidence: float
    seen_at: datetime
    image_url: str | None
    person_id: int | None = None
    unknown_id: int | None = None
    device_id: int | None = None
    device_name: str | None = None


class DeviceOut(BaseModel):
    id: int
    name: str
    recording_enabled: bool
    online: bool
    created_at: datetime
    last_seen: datetime | None = None


class PairingSessionOut(BaseModel):
    token: str
    qr_payload: str
    expires_at: datetime


class PairDeviceIn(BaseModel):
    token: str = Field(min_length=20, max_length=128)
    name: str = Field(min_length=1, max_length=120)


class PairingStatusOut(BaseModel):
    paired: bool
    expires_at: datetime
    device: DeviceOut | None = None


class RecordingOut(BaseModel):
    id: int
    device_id: int
    device_name: str
    started_at: datetime
    ended_at: datetime
    frame_count: int
    video_url: str


class DetectionOut(BaseModel):
    kind: str
    label: str
    confidence: float
    box: list[int]
    unknown_id: int | None = None
    alert_created: bool = False


class LiveFeedOut(BaseModel):
    online: bool
    frame_data_url: str | None = None
    detections: list[DetectionOut] = Field(default_factory=list)
    captured_at: datetime | None = None
    source_session_id: str | None = None
    device_id: int | None = None
    device_name: str | None = None


import asyncio
import json
import secrets
import shutil
import uuid
from datetime import timedelta, timezone
from pathlib import Path

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from .auth import current_user, hash_password, make_token, user_id_from_token, verify_password
from .config import get_settings
from .database import Base, engine, get_db
from .face_engine import EMBEDDING_MODEL, locate_faces, one_face_embedding, pack_embedding
from .live_feed import live_feed_service
from .live_stream import live_stream_service
from .model_migration import migrate_face_embeddings
from .models import (
    DetectionEvent,
    Device,
    DevicePairingSession,
    KnownFace,
    Person,
    Recording,
    UnknownIdentity,
    User,
    utcnow,
)
from .notifier import send_unknown_alert
from .recognition import recognition_service
from .recording import CompletedRecording, recording_service
from .schemas import (
    ActivityOut,
    AuthOut,
    DetectionOut,
    DeviceOut,
    EventOut,
    LiveFeedOut,
    LoginIn,
    PairDeviceIn,
    PairingSessionOut,
    PairingStatusOut,
    PersonOut,
    RecordingOut,
    RegisterIn,
    UnknownOut,
    UserOut,
)

settings = get_settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(engine)
migrate_face_embeddings(engine)

app = FastAPI(title=settings.app_name, version="2.3.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/media", StaticFiles(directory=settings.storage_dir), name="media")


def media_url(request: Request, path: str | None) -> str | None:
    if not path:
        return None
    relative = Path(path).resolve().relative_to(settings.storage_dir.resolve()).as_posix()
    return str(request.base_url).rstrip("/") + "/media/" + relative


def remove_media(path_value: str | None) -> None:
    if not path_value:
        return
    try:
        path = Path(path_value).resolve()
        path.relative_to(settings.storage_dir.resolve())
        path.unlink(missing_ok=True)
    except (OSError, ValueError):
        return


def ensure_default_device(db: Session, owner_id: int) -> Device:
    device = db.scalar(select(Device).where(Device.owner_id == owner_id).order_by(Device.id))
    if device:
        return device
    device = Device(owner_id=owner_id, name="Laptop Camera")
    db.add(device)
    db.commit()
    db.refresh(device)
    return device


def owned_device(db: Session, owner_id: int, device_id: int | None) -> Device:
    if device_id is None:
        return ensure_default_device(db, owner_id)
    device = db.scalar(select(Device).where(Device.id == device_id, Device.owner_id == owner_id))
    if not device:
        raise HTTPException(404, "Device not found")
    return device


def pairing_expired(session: DevicePairingSession) -> bool:
    expires_at = session.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at <= utcnow()


async def device_out(db: Session, device: Device) -> DeviceOut:
    online = device.id in await live_stream_service.online_devices(device.owner_id)
    return DeviceOut(
        id=device.id,
        name=device.name,
        recording_enabled=device.recording_enabled,
        online=online,
        created_at=device.created_at,
        last_seen=device.last_seen,
    )


def activity_out(request: Request, event: DetectionEvent,
                 device_names: dict[int, str]) -> ActivityOut:
    return ActivityOut(
        id=event.id,
        label=event.label,
        confidence=event.confidence,
        seen_at=event.seen_at,
        image_url=media_url(request, event.image_path),
        device_id=event.device_id,
        device_name=device_names.get(event.device_id) if event.device_id else None,
    )


def person_out(request: Request, person: Person, events: list[DetectionEvent],
               device_names: dict[int, str]) -> PersonOut:
    recent = sorted(events, key=lambda event: event.seen_at, reverse=True)
    return PersonOut(
        id=person.id,
        name=person.name,
        notes=person.notes,
        face_count=len(person.faces),
        created_at=person.created_at,
        appearances=len(recent),
        last_seen=recent[0].seen_at if recent else None,
        recent_activities=[activity_out(request, event, device_names) for event in recent[:10]],
    )


def unknown_out(request: Request, item: UnknownIdentity, events: list[DetectionEvent],
                device_names: dict[int, str]) -> UnknownOut:
    recent = sorted(events, key=lambda event: event.seen_at, reverse=True)
    return UnknownOut(
        id=item.id,
        label=item.label,
        first_seen=item.first_seen,
        last_seen=item.last_seen,
        sightings=max(item.sightings, len(recent)),
        image_url=media_url(request, item.image_path),
        activities=[activity_out(request, event, device_names) for event in recent[:20]],
    )


def persist_recordings(db: Session, completed: list[CompletedRecording]) -> None:
    for item in completed:
        db.add(Recording(
            owner_id=item.owner_id,
            device_id=item.device_id,
            file_path=item.file_path,
            started_at=item.started_at,
            ended_at=item.ended_at,
            frame_count=item.frame_count,
        ))
    if completed:
        db.commit()


async def prepare_face_photos(photos: list[UploadFile]) -> list[tuple[bytes, bytes]]:
    if not 1 <= len(photos) <= 12:
        raise HTTPException(400, "Select 1-12 clear photos")
    prepared = []
    for index, photo in enumerate(photos, start=1):
        data = await photo.read()
        if len(data) > 10 * 1024 * 1024:
            raise HTTPException(413, f"Photo {index} must be under 10 MB")
        try:
            prepared.append((data, pack_embedding(one_face_embedding(data))))
        except ValueError as exc:
            raise HTTPException(422, f"Photo {index}: {exc}")
    return prepared


def store_face_photos(db: Session, owner_id: int, person_id: int,
                      prepared: list[tuple[bytes, bytes]]) -> None:
    folder = settings.storage_dir / str(owner_id) / "known" / str(person_id)
    folder.mkdir(parents=True, exist_ok=True)
    for data, embedding in prepared:
        path = folder / f"{uuid.uuid4().hex}.jpg"
        path.write_bytes(data)
        db.add(KnownFace(
            person_id=person_id,
            image_path=str(path),
            embedding=embedding,
            embedding_model=EMBEDDING_MODEL,
        ))


async def websocket_auth(websocket: WebSocket, db: Session) -> tuple[User, dict] | None:
    await websocket.accept()
    try:
        message = await asyncio.wait_for(websocket.receive_json(), timeout=10)
        if message.get("type") != "auth" or not isinstance(message.get("token"), str):
            raise ValueError("Missing token")
        user = db.get(User, user_id_from_token(message["token"]))
        if not user:
            raise ValueError("Unknown user")
        return user, message
    except Exception:
        await websocket.close(code=1008, reason="Authentication failed")
        return None


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/auth/register", response_model=AuthOut, status_code=201)
def register(payload: RegisterIn, db: Session = Depends(get_db)):
    email = payload.email.lower()
    if db.scalar(select(User).where(User.email == email)):
        raise HTTPException(409, "An account with this email already exists")
    user = User(
        email=email,
        display_name=payload.display_name.strip(),
        password_hash=hash_password(payload.password),
    )
    db.add(user)
    db.flush()
    db.add(Device(owner_id=user.id, name="Laptop Camera"))
    db.commit()
    db.refresh(user)
    return AuthOut(access_token=make_token(user.id), user=user)


@app.post("/auth/login", response_model=AuthOut)
def login(payload: LoginIn, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == payload.email.lower()))
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(401, "Incorrect email or password")
    return AuthOut(access_token=make_token(user.id), user=user)


@app.get("/auth/me", response_model=UserOut)
def me(user: User = Depends(current_user)):
    return user


@app.get("/devices", response_model=list[DeviceOut])
async def list_devices(db: Session = Depends(get_db), user: User = Depends(current_user)):
    rows = db.scalars(select(Device).where(Device.owner_id == user.id).order_by(Device.created_at)).all()
    online = await live_stream_service.online_devices(user.id)
    return [DeviceOut(
        id=row.id,
        name=row.name,
        recording_enabled=row.recording_enabled,
        online=row.id in online,
        created_at=row.created_at,
        last_seen=row.last_seen,
    ) for row in rows]


@app.post("/devices/pairing-sessions", response_model=PairingSessionOut, status_code=201)
def create_pairing_session(db: Session = Depends(get_db), user: User = Depends(current_user)):
    token_value = secrets.token_urlsafe(32)
    expires_at = utcnow() + timedelta(minutes=5)
    session = DevicePairingSession(
        token=token_value,
        owner_id=user.id,
        expires_at=expires_at,
    )
    db.add(session)
    db.commit()
    payload = json.dumps({
        "type": "face-recognition-device-pair",
        "version": 1,
        "token": token_value,
    }, separators=(",", ":"))
    return PairingSessionOut(token=token_value, qr_payload=payload, expires_at=expires_at)


@app.get("/devices/pairing-sessions/{token_value}", response_model=PairingStatusOut)
async def pairing_status(token_value: str, db: Session = Depends(get_db),
                         user: User = Depends(current_user)):
    session = db.scalar(select(DevicePairingSession).where(
        DevicePairingSession.token == token_value,
        DevicePairingSession.owner_id == user.id,
    ))
    if not session:
        raise HTTPException(404, "Pairing session not found")
    if pairing_expired(session) and session.device_id is None:
        raise HTTPException(410, "This QR code has expired")
    device = db.get(Device, session.device_id) if session.device_id else None
    return PairingStatusOut(
        paired=device is not None,
        expires_at=session.expires_at,
        device=await device_out(db, device) if device else None,
    )


@app.post("/devices/pair", response_model=DeviceOut, status_code=201)
async def pair_device(payload: PairDeviceIn, db: Session = Depends(get_db),
                      user: User = Depends(current_user)):
    session = db.scalar(select(DevicePairingSession).where(
        DevicePairingSession.token == payload.token,
    ))
    if not session or session.owner_id != user.id:
        raise HTTPException(403, "This QR code belongs to a different account")
    if pairing_expired(session) and session.device_id is None:
        raise HTTPException(410, "This QR code has expired. Generate a new one on the laptop")
    if session.device_id:
        existing = db.get(Device, session.device_id)
        if existing:
            return await device_out(db, existing)
    cleaned = payload.name.strip()
    if not cleaned:
        raise HTTPException(400, "Provide a device name")
    if db.scalar(select(Device).where(Device.owner_id == user.id, Device.name == cleaned)):
        raise HTTPException(409, "A device with this name already exists")
    device = Device(owner_id=user.id, name=cleaned)
    db.add(device)
    db.flush()
    session.device_id = device.id
    session.claimed_at = utcnow()
    db.commit()
    db.refresh(device)
    return await device_out(db, device)


@app.post("/devices", response_model=DeviceOut, status_code=201)
def add_device(name: str = Form(...), db: Session = Depends(get_db),
               user: User = Depends(current_user)):
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(400, "Provide a device name")
    if db.scalar(select(Device).where(Device.owner_id == user.id, Device.name == cleaned)):
        raise HTTPException(409, "A device with this name already exists")
    device = Device(owner_id=user.id, name=cleaned)
    db.add(device)
    db.commit()
    db.refresh(device)
    return DeviceOut(
        id=device.id,
        name=device.name,
        recording_enabled=False,
        online=False,
        created_at=device.created_at,
        last_seen=device.last_seen,
    )


@app.delete("/devices/{device_id}", status_code=204)
async def delete_device(device_id: int, db: Session = Depends(get_db),
                        user: User = Depends(current_user)):
    device = owned_device(db, user.id, device_id)
    if device.id in await live_stream_service.online_devices(user.id):
        raise HTTPException(409, "Stop this camera source before removing the device")
    completed = await asyncio.to_thread(recording_service.set_enabled, user.id, device.id, False)
    persist_recordings(db, completed)
    recordings = db.scalars(select(Recording).where(
        Recording.owner_id == user.id,
        Recording.device_id == device.id,
    )).all()
    recording_paths = [item.file_path for item in recordings]
    for event in db.scalars(select(DetectionEvent).where(
        DetectionEvent.owner_id == user.id,
        DetectionEvent.device_id == device.id,
    )).all():
        event.device_id = None
    for session in db.scalars(select(DevicePairingSession).where(
        DevicePairingSession.owner_id == user.id,
        DevicePairingSession.device_id == device.id,
    )).all():
        db.delete(session)
    for recording in recordings:
        db.delete(recording)
    db.delete(device)
    db.commit()
    for path in recording_paths:
        remove_media(path)


@app.patch("/devices/{device_id}/recording", response_model=DeviceOut)
async def set_device_recording(device_id: int, enabled: bool = Form(...),
                               db: Session = Depends(get_db), user: User = Depends(current_user)):
    device = owned_device(db, user.id, device_id)
    device.recording_enabled = enabled
    completed = await asyncio.to_thread(recording_service.set_enabled, user.id, device.id, enabled)
    persist_recordings(db, completed)
    db.add(device)
    db.commit()
    online = device.id in await live_stream_service.online_devices(user.id)
    return DeviceOut(
        id=device.id,
        name=device.name,
        recording_enabled=device.recording_enabled,
        online=online,
        created_at=device.created_at,
        last_seen=device.last_seen,
    )


@app.get("/recordings", response_model=list[RecordingOut])
def list_recordings(request: Request, db: Session = Depends(get_db),
                    user: User = Depends(current_user)):
    rows = db.execute(
        select(Recording, Device).join(Device).where(Recording.owner_id == user.id)
        .order_by(Recording.started_at.desc()).limit(200)
    ).all()
    return [RecordingOut(
        id=recording.id,
        device_id=device.id,
        device_name=device.name,
        started_at=recording.started_at,
        ended_at=recording.ended_at,
        frame_count=recording.frame_count,
        video_url=media_url(request, recording.file_path),
    ) for recording, device in rows]


@app.get("/people", response_model=list[PersonOut])
def list_people(request: Request, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    people = db.scalars(select(Person).where(Person.owner_id == user.id).order_by(Person.name)).all()
    events = db.scalars(select(DetectionEvent).where(
        DetectionEvent.owner_id == user.id, DetectionEvent.person_id.is_not(None)
    )).all()
    grouped: dict[int, list[DetectionEvent]] = {}
    for event in events:
        if event.person_id:
            grouped.setdefault(event.person_id, []).append(event)
    device_names = {row.id: row.name for row in db.scalars(
        select(Device).where(Device.owner_id == user.id)
    ).all()}
    return [person_out(request, person, grouped.get(person.id, []), device_names) for person in people]


@app.post("/people", response_model=PersonOut, status_code=201)
async def add_person(request: Request, name: str = Form(...), notes: str | None = Form(None),
                     photos: list[UploadFile] = File(...), db: Session = Depends(get_db),
                     user: User = Depends(current_user)):
    if not name.strip():
        raise HTTPException(400, "Provide a person's name")
    prepared = await prepare_face_photos(photos)
    person = Person(owner_id=user.id, name=name.strip(), notes=notes)
    db.add(person)
    db.flush()
    store_face_photos(db, user.id, person.id, prepared)
    db.commit()
    db.refresh(person)
    return person_out(request, person, [], {})


@app.post("/people/{person_id}/photos", response_model=PersonOut)
async def add_person_photos(person_id: int, request: Request,
                            photos: list[UploadFile] = File(...), db: Session = Depends(get_db),
                            user: User = Depends(current_user)):
    person = db.scalar(select(Person).where(Person.id == person_id, Person.owner_id == user.id))
    if not person:
        raise HTTPException(404, "Person not found")
    prepared = await prepare_face_photos(photos)
    store_face_photos(db, user.id, person.id, prepared)
    db.commit()
    db.refresh(person)
    events = db.scalars(select(DetectionEvent).where(DetectionEvent.person_id == person.id)).all()
    device_names = {row.id: row.name for row in db.scalars(
        select(Device).where(Device.owner_id == user.id)
    ).all()}
    return person_out(request, person, list(events), device_names)


@app.delete("/people/{person_id}", status_code=204)
def delete_person(person_id: int, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    person = db.scalar(select(Person).where(Person.id == person_id, Person.owner_id == user.id))
    if not person:
        raise HTTPException(404, "Person not found")
    events = db.scalars(select(DetectionEvent).where(
        DetectionEvent.owner_id == user.id, DetectionEvent.person_id == person.id
    )).all()
    event_paths = [event.image_path for event in events]
    for event in events:
        db.delete(event)
    db.delete(person)
    db.commit()
    folder = settings.storage_dir / str(user.id) / "known" / str(person_id)
    if folder.exists():
        shutil.rmtree(folder)
    for path in event_paths:
        remove_media(path)


@app.post("/recognition/frame", response_model=list[DetectionOut])
async def recognize_frame(background: BackgroundTasks, session_id: str = Form(...),
                          device_id: int | None = Form(None), frame: UploadFile = File(...),
                          db: Session = Depends(get_db), user: User = Depends(current_user)):
    device = owned_device(db, user.id, device_id)
    data = await frame.read()
    if len(data) > 5 * 1024 * 1024:
        raise HTTPException(413, "Frame must be under 5 MB")
    live_feed_service.publish(user.id, device.id, session_id, data)
    try:
        rgb, faces = await asyncio.to_thread(locate_faces, data)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    detections = recognition_service.recognize(db, user, session_id, device.id, rgb, faces)
    device.last_seen = utcnow()
    db.add(device)
    db.commit()
    live_feed_service.update_detections(user.id, device.id, session_id, detections)
    await live_stream_service.publish_detections(user.id, device.id, detections)
    if user.alerts_enabled:
        for item in detections:
            if item["alert_created"]:
                unknown = db.get(UnknownIdentity, item["unknown_id"])
                if unknown:
                    background.add_task(send_unknown_alert, user.email, unknown.label, unknown.image_path)
    return detections


@app.get("/recognition/live", response_model=LiveFeedOut)
def live_feed(device_id: int | None = None, db: Session = Depends(get_db),
              user: User = Depends(current_user)):
    device = owned_device(db, user.id, device_id) if device_id is not None else None
    frame, online = live_feed_service.latest(user.id, device.id if device else None)
    if not frame:
        return LiveFeedOut(online=False, device_id=device.id if device else None,
                           device_name=device.name if device else None)
    source_device = db.get(Device, frame.device_id)
    return LiveFeedOut(
        online=online,
        frame_data_url=frame.data_url,
        detections=frame.detections,
        captured_at=frame.captured_at,
        source_session_id=frame.session_id,
        device_id=frame.device_id,
        device_name=source_device.name if source_device else None,
    )


@app.websocket("/ws/live/source")
async def live_source(websocket: WebSocket, db: Session = Depends(get_db)):
    auth = await websocket_auth(websocket, db)
    if not auth:
        return
    user, message = auth
    try:
        device_id = int(message.get("device_id")) if message.get("device_id") is not None else None
        device = owned_device(db, user.id, device_id)
    except (HTTPException, TypeError, ValueError):
        await websocket.close(code=1008, reason="Unknown device")
        return
    device.last_seen = utcnow()
    db.commit()
    await asyncio.to_thread(
        recording_service.set_enabled, user.id, device.id, device.recording_enabled
    )
    await live_stream_service.source_connected(user.id, device.id)
    try:
        while True:
            data_url = await websocket.receive_text()
            if data_url.startswith("data:image/jpeg;base64,") and len(data_url) <= 1_500_000:
                completed = await asyncio.to_thread(
                    recording_service.accept_frame, user.id, device.id, data_url
                )
                persist_recordings(db, completed)
                await live_stream_service.publish_frame(user.id, device.id, data_url)
    except WebSocketDisconnect:
        pass
    finally:
        completed = await asyncio.to_thread(
            recording_service.source_disconnected, user.id, device.id
        )
        persist_recordings(db, completed)
        await live_stream_service.source_disconnected(user.id, device.id)


@app.websocket("/ws/live/viewer")
async def live_viewer(websocket: WebSocket, db: Session = Depends(get_db)):
    auth = await websocket_auth(websocket, db)
    if not auth:
        return
    user, _ = auth
    await live_stream_service.add_viewer(user.id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await live_stream_service.remove_viewer(user.id, websocket)


@app.get("/unknowns", response_model=list[UnknownOut])
def list_unknowns(request: Request, db: Session = Depends(get_db),
                  user: User = Depends(current_user)):
    rows = db.scalars(select(UnknownIdentity).where(UnknownIdentity.owner_id == user.id)
                      .order_by(UnknownIdentity.last_seen.desc())).all()
    events = db.scalars(select(DetectionEvent).where(
        DetectionEvent.owner_id == user.id, DetectionEvent.unknown_id.is_not(None)
    )).all()
    grouped: dict[int, list[DetectionEvent]] = {}
    for event in events:
        if event.unknown_id:
            grouped.setdefault(event.unknown_id, []).append(event)
    device_names = {row.id: row.name for row in db.scalars(
        select(Device).where(Device.owner_id == user.id)
    ).all()}
    return [unknown_out(request, item, grouped.get(item.id, []), device_names) for item in rows]


@app.patch("/unknowns/{unknown_id}/label", response_model=UnknownOut)
def label_unknown(unknown_id: int, request: Request, label: str = Form(...),
                  db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.scalar(select(UnknownIdentity).where(
        UnknownIdentity.id == unknown_id, UnknownIdentity.owner_id == user.id
    ))
    if not item:
        raise HTTPException(404, "Unknown identity not found")
    item.label = label.strip() or item.label
    events = db.scalars(select(DetectionEvent).where(DetectionEvent.unknown_id == item.id)).all()
    for event in events:
        event.label = item.label
    db.commit()
    db.refresh(item)
    device_names = {row.id: row.name for row in db.scalars(
        select(Device).where(Device.owner_id == user.id)
    ).all()}
    return unknown_out(request, item, list(events), device_names)


@app.post("/unknowns/{unknown_id}/promote", response_model=PersonOut)
def promote_unknown(unknown_id: int, request: Request, name: str = Form(...),
                    db: Session = Depends(get_db), user: User = Depends(current_user)):
    item = db.scalar(select(UnknownIdentity).where(
        UnknownIdentity.id == unknown_id, UnknownIdentity.owner_id == user.id
    ))
    if not item:
        raise HTTPException(404, "Unknown identity not found")
    cleaned = name.strip()
    if not cleaned:
        raise HTTPException(400, "Provide the person's name")
    source = Path(item.image_path)
    if not source.is_file():
        raise HTTPException(409, "The saved snapshot is missing")
    person = Person(owner_id=user.id, name=cleaned, notes="Added from unknown visitor")
    db.add(person)
    db.flush()
    folder = settings.storage_dir / str(user.id) / "known" / str(person.id)
    folder.mkdir(parents=True, exist_ok=True)
    destination = folder / f"{uuid.uuid4().hex}.jpg"
    shutil.copy2(source, destination)
    db.add(KnownFace(
        person_id=person.id,
        image_path=str(destination),
        embedding=item.embedding,
        embedding_model=item.embedding_model or EMBEDDING_MODEL,
    ))
    events = db.scalars(select(DetectionEvent).where(DetectionEvent.unknown_id == item.id)).all()
    for event in events:
        event.person_id = person.id
        event.unknown_id = None
        event.label = person.name
    db.delete(item)
    db.commit()
    db.refresh(person)
    device_names = {row.id: row.name for row in db.scalars(
        select(Device).where(Device.owner_id == user.id)
    ).all()}
    return person_out(request, person, list(events), device_names)


@app.delete("/unknowns/{unknown_id}", status_code=204)
def delete_unknown(unknown_id: int, db: Session = Depends(get_db),
                   user: User = Depends(current_user)):
    item = db.scalar(select(UnknownIdentity).where(
        UnknownIdentity.id == unknown_id, UnknownIdentity.owner_id == user.id
    ))
    if not item:
        raise HTTPException(404, "Unknown identity not found")
    events = db.scalars(select(DetectionEvent).where(DetectionEvent.unknown_id == item.id)).all()
    paths = {item.image_path, *(event.image_path for event in events if event.image_path)}
    for event in events:
        db.delete(event)
    db.delete(item)
    db.commit()
    for path in paths:
        remove_media(path)


@app.get("/events", response_model=list[EventOut])
def list_events(request: Request, limit: int = 50, db: Session = Depends(get_db),
                user: User = Depends(current_user)):
    rows = db.scalars(select(DetectionEvent).where(DetectionEvent.owner_id == user.id)
                      .order_by(DetectionEvent.seen_at.desc()).limit(min(limit, 200))).all()
    device_names = {row.id: row.name for row in db.scalars(
        select(Device).where(Device.owner_id == user.id)
    ).all()}
    return [EventOut(
        id=item.id,
        label=item.label,
        confidence=item.confidence,
        seen_at=item.seen_at,
        image_url=media_url(request, item.image_path),
        person_id=item.person_id,
        unknown_id=item.unknown_id,
        device_id=item.device_id,
        device_name=device_names.get(item.device_id) if item.device_id else None,
    ) for item in rows]

import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .face_engine import (
    EMBEDDING_MODEL,
    LocatedFace,
    confidence_from_similarity,
    normalize_embedding,
    pack_embedding,
    similarity,
    unpack_embedding,
)
from .models import DetectionEvent, KnownFace, Person, UnknownIdentity, User, utcnow


@dataclass
class FaceTrack:
    first_seen: float
    last_seen: float
    embedding: np.ndarray
    observations: int = 1
    candidate_key: tuple[str, int] | None = None
    candidate_streak: int = 0
    unmatched_since: float | None = None
    alerted: bool = False
    event_logged: bool = False


class RecognitionService:
    def __init__(self):
        self._tracks: dict[tuple[int, int, str], list[FaceTrack]] = {}
        self._known_event_times: dict[tuple[int, int, int], float] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _best_single(probe: np.ndarray, candidates: list[tuple[object, np.ndarray]]):
        if not candidates:
            return None, -1.0
        return max(((item, similarity(probe, enc)) for item, enc in candidates), key=lambda row: row[1])

    @staticmethod
    def _rank_people(probe: np.ndarray, candidates: dict[int, tuple[Person, list[np.ndarray]]]):
        ranked: list[tuple[Person, float]] = []
        for person, embeddings in candidates.values():
            scores = sorted((similarity(probe, embedding) for embedding in embeddings), reverse=True)
            if not scores:
                continue
            # Preserve a strong matching angle while requiring support from the
            # person's other uploaded views when they are available.
            supporting = float(np.mean(scores[: min(3, len(scores))]))
            combined = scores[0] if len(scores) == 1 else 0.7 * scores[0] + 0.3 * supporting
            ranked.append((person, combined))
        return sorted(ranked, key=lambda row: row[1], reverse=True)

    @staticmethod
    def _confirmed(track: FaceTrack, key: tuple[str, int], required: int) -> bool:
        if track.candidate_key == key:
            track.candidate_streak += 1
        else:
            track.candidate_key = key
            track.candidate_streak = 1
        return track.candidate_streak >= required

    def recognize(self, db: Session, user: User, session_id: str, device_id: int,
                  rgb: np.ndarray, faces: list[LocatedFace]):
        settings = get_settings()
        known_rows = db.execute(
            select(KnownFace, Person).join(Person).where(
                Person.owner_id == user.id,
                KnownFace.embedding_model == EMBEDDING_MODEL,
            )
        ).all()
        unknown_rows = db.scalars(
            select(UnknownIdentity).where(
                UnknownIdentity.owner_id == user.id,
                UnknownIdentity.embedding_model == EMBEDDING_MODEL,
            )
        ).all()

        known: dict[int, tuple[Person, list[np.ndarray]]] = {}
        for face, person in known_rows:
            entry = known.setdefault(person.id, (person, []))
            entry[1].append(unpack_embedding(face.embedding))
        unknown = [(item, unpack_embedding(item.embedding)) for item in unknown_rows]

        output = []
        now_mono = time.monotonic()
        key = (user.id, device_id, session_id)

        with self._lock:
            tracks = self._tracks.setdefault(key, [])
            tracks[:] = [track for track in tracks if now_mono - track.last_seen < settings.face_track_expiry_seconds]
            available_tracks = list(tracks)

            for found in faces:
                track, track_score = self._best_single(
                    found.embedding, [(item, item.embedding) for item in available_tracks]
                )
                if track is None or track_score < settings.face_track_threshold:
                    track = FaceTrack(now_mono, now_mono, found.embedding)
                    tracks.append(track)
                else:
                    available_tracks.remove(track)
                    track.last_seen = now_mono
                    track.observations += 1
                    track.embedding = normalize_embedding(0.75 * track.embedding + 0.25 * found.embedding)

                ranked = self._rank_people(found.embedding, known)
                best_person, best_score = ranked[0] if ranked else (None, -1.0)
                second_score = ranked[1][1] if len(ranked) > 1 else -1.0
                known_match = (
                    best_person is not None
                    and best_score >= settings.face_match_threshold
                    and (len(ranked) == 1 or best_score - second_score >= settings.face_match_margin)
                )

                if known_match:
                    track.unmatched_since = None
                    confirmed = self._confirmed(
                        track, ("known", best_person.id), settings.face_confirmation_frames
                    )
                    event_key = (user.id, device_id, best_person.id)
                    previous_event = self._known_event_times.get(event_key, -999999.0)
                    if confirmed and not track.event_logged:
                        if now_mono - previous_event >= settings.activity_cooldown_seconds:
                            db.add(DetectionEvent(
                                owner_id=user.id,
                                person_id=best_person.id,
                                device_id=device_id,
                                label=best_person.name,
                                confidence=confidence_from_similarity(best_score, settings.face_match_threshold),
                            ))
                            self._known_event_times[event_key] = now_mono
                        track.event_logged = True
                    output.append({
                        "kind": "known" if confirmed else "pending",
                        "label": best_person.name if confirmed else f"Verifying {best_person.name}",
                        "confidence": confidence_from_similarity(best_score, settings.face_match_threshold),
                        "box": list(found.box),
                        "unknown_id": None,
                        "alert_created": False,
                    })
                    continue

                previous, old_score = self._best_single(found.embedding, unknown)
                if previous is not None and old_score >= settings.unknown_match_threshold:
                    track.unmatched_since = None
                    confirmed = self._confirmed(
                        track, ("unknown", previous.id), settings.face_confirmation_frames
                    )
                    if confirmed and not track.event_logged:
                        previous.last_seen = utcnow()
                        previous.sightings += 1
                        event_path = self._save_event_snapshot(user.id, rgb, found, "unknown-events")
                        db.add(DetectionEvent(
                            owner_id=user.id,
                            unknown_id=previous.id,
                            device_id=device_id,
                            label=previous.label,
                            confidence=confidence_from_similarity(old_score, settings.unknown_match_threshold),
                            image_path=str(event_path),
                        ))
                        track.event_logged = True
                    output.append({
                        "kind": "unknown" if confirmed else "pending",
                        "label": previous.label if confirmed else "Verifying previous visitor",
                        "confidence": confidence_from_similarity(old_score, settings.unknown_match_threshold),
                        "box": list(found.box),
                        "unknown_id": previous.id if confirmed else None,
                        "alert_created": False,
                    })
                    continue

                track.candidate_key = None
                track.candidate_streak = 0
                if track.unmatched_since is None:
                    track.unmatched_since = now_mono
                elapsed = now_mono - track.unmatched_since
                result = {
                    "kind": "pending",
                    "label": f"Unknown ({elapsed:.1f}s)",
                    "confidence": 0.0,
                    "box": list(found.box),
                    "unknown_id": None,
                    "alert_created": False,
                }
                if (
                    elapsed >= settings.unknown_hold_seconds
                    and track.observations >= settings.face_confirmation_frames
                    and not track.alerted
                ):
                    smoothed = LocatedFace(track.embedding, found.box, found.detection_score)
                    identity = self._save_unknown(db, user.id, device_id, rgb, smoothed)
                    track.alerted = True
                    track.event_logged = True
                    track.candidate_key = ("unknown", identity.id)
                    result.update(
                        kind="unknown",
                        label=identity.label,
                        unknown_id=identity.id,
                        alert_created=True,
                    )
                output.append(result)
        db.commit()
        return output

    def _save_event_snapshot(self, owner_id: int, rgb: np.ndarray, face: LocatedFace,
                             category: str) -> Path:
        settings = get_settings()
        user_dir = settings.storage_dir / str(owner_id) / category
        user_dir.mkdir(parents=True, exist_ok=True)
        top, right, bottom, left = face.box
        pad = max(12, int((bottom - top) * 0.3))
        crop = rgb[
            max(0, top - pad):min(rgb.shape[0], bottom + pad),
            max(0, left - pad):min(rgb.shape[1], right + pad),
        ]
        filename = f"{uuid.uuid4().hex}.jpg"
        path = user_dir / filename
        Image.fromarray(crop).save(path, "JPEG", quality=92)
        return path

    def _save_unknown(self, db: Session, owner_id: int, device_id: int,
                      rgb: np.ndarray, face: LocatedFace) -> UnknownIdentity:
        path = self._save_event_snapshot(owner_id, rgb, face, "unknown")
        item = UnknownIdentity(
            owner_id=owner_id,
            label="pending",
            image_path=str(path),
            embedding=pack_embedding(face.embedding),
            embedding_model=EMBEDDING_MODEL,
        )
        db.add(item)
        db.flush()
        item.label = f"Unknown #{item.id}"
        db.add(DetectionEvent(
            owner_id=owner_id,
            unknown_id=item.id,
            device_id=device_id,
            label=item.label,
            confidence=0.0,
            image_path=str(path),
        ))
        return item


recognition_service = RecognitionService()

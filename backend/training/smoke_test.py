"""Local end-to-end smoke test for model loading, migration, and matching."""

from __future__ import annotations

import os
import base64
import sys
import tempfile
import time
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.database import Base  # noqa: E402
from app.face_engine import EMBEDDING_MODEL, locate_faces, one_face_embedding, pack_embedding  # noqa: E402
from app.model_migration import migrate_face_embeddings  # noqa: E402
from app.models import DetectionEvent, Device, KnownFace, Person, UnknownIdentity, User  # noqa: E402


def folders_with_images(dataset: Path, minimum: int = 3):
    result = []
    for folder in dataset.rglob("*"):
        images = sorted(folder.glob("*.jpg")) if folder.is_dir() else []
        if len(images) >= minimum:
            result.append(images)
    return result


def main() -> None:
    dataset = BACKEND_DIR / "training-data" / "lfw"
    identities = folders_with_images(dataset)
    if len(identities) < 3:
        raise SystemExit("The extracted LFW dataset was not found")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        os.environ["STORAGE_DIR"] = str(root / "uploads")
        os.environ["UNKNOWN_HOLD_SECONDS"] = "0.05"
        from app.config import get_settings
        from app.recognition import RecognitionService
        from app.recording import RecordingService

        get_settings.cache_clear()
        engine = create_engine(f"sqlite:///{(root / 'test.db').as_posix()}")
        Base.metadata.create_all(engine)

        with Session(engine) as db:
            user = User(email="smoke@example.com", display_name="Smoke", password_hash="unused")
            db.add(user)
            db.flush()
            device = Device(owner_id=user.id, name="Test Camera")
            db.add(device)
            db.flush()
            device_id = device.id
            people = []
            for index, images in enumerate(identities[:2], start=1):
                person = Person(owner_id=user.id, name=f"Person {index}")
                db.add(person)
                db.flush()
                people.append(person)
                for image in images[:2]:
                    db.add(KnownFace(
                        person_id=person.id,
                        image_path=str(image),
                        embedding=pack_embedding(one_face_embedding(image.read_bytes())),
                        embedding_model=EMBEDDING_MODEL,
                    ))
            # Simulate one record created by the previous dlib backend.
            legacy_image = identities[0][2]
            legacy = KnownFace(
                person_id=people[0].id,
                image_path=str(legacy_image),
                embedding=bytes(128 * 4),
                embedding_model=None,
            )
            db.add(legacy)
            db.flush()
            legacy_id = legacy.id
            first_person_id = people[0].id
            db.commit()

        migrate_face_embeddings(engine)
        with Session(engine) as db:
            migrated = db.scalar(select(KnownFace).where(KnownFace.id == legacy_id))
            assert migrated is not None and migrated.embedding_model == EMBEDDING_MODEL
            user = db.scalar(select(User).where(User.email == "smoke@example.com"))
            assert user is not None
            service = RecognitionService()

            known_rgb, known_faces = locate_faces(identities[0][2].read_bytes())
            assert len(known_faces) == 1
            first = service.recognize(db, user, "known", device_id, known_rgb, known_faces)
            second = service.recognize(db, user, "known", device_id, known_rgb, known_faces)
            assert first[0]["kind"] == "pending"
            assert second[0]["kind"] == "known" and second[0]["label"] == "Person 1"
            known_event = db.scalar(select(DetectionEvent).where(DetectionEvent.person_id == first_person_id))
            assert known_event is not None and known_event.device_id == device_id

            unknown_rgb, unknown_faces = locate_faces(identities[2][0].read_bytes())
            assert len(unknown_faces) == 1
            service.recognize(db, user, "unknown", device_id, unknown_rgb, unknown_faces)
            time.sleep(0.06)
            result = service.recognize(db, user, "unknown", device_id, unknown_rgb, unknown_faces)
            assert result[0]["kind"] == "unknown" and result[0]["alert_created"]
            saved = db.scalar(select(UnknownIdentity))
            assert saved is not None and saved.embedding_model == EMBEDDING_MODEL
            unknown_event = db.scalar(select(DetectionEvent).where(DetectionEvent.unknown_id == saved.id))
            assert unknown_event is not None and unknown_event.device_id == device_id

            recorder = RecordingService()
            recorder.set_enabled(user.id, device_id, True)
            encoded = base64.b64encode(identities[0][0].read_bytes()).decode("ascii")
            for _ in range(3):
                recorder.accept_frame(user.id, device_id, f"data:image/jpeg;base64,{encoded}")
            completed = recorder.set_enabled(user.id, device_id, False)
            assert len(completed) == 1 and completed[0].frame_count == 3
            assert Path(completed[0].file_path).is_file()
        engine.dispose()

    print("Face backend smoke test passed")


if __name__ == "__main__":
    main()

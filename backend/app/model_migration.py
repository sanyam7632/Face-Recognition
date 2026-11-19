from pathlib import Path

from sqlalchemy import Engine, inspect, select, text
from sqlalchemy.orm import Session

from .face_engine import EMBEDDING_MODEL, one_face_embedding, pack_embedding
from .models import KnownFace, UnknownIdentity


def _ensure_model_columns(engine: Engine) -> None:
    inspector = inspect(engine)
    with engine.begin() as connection:
        for table_name in ("known_faces", "unknown_identities"):
            columns = {column["name"] for column in inspector.get_columns(table_name)}
            if "embedding_model" not in columns:
                connection.execute(text(
                    f"ALTER TABLE {table_name} ADD COLUMN embedding_model VARCHAR(40)"
                ))
        event_columns = {column["name"] for column in inspector.get_columns("detection_events")}
        if "device_id" not in event_columns:
            connection.execute(text(
                "ALTER TABLE detection_events ADD COLUMN device_id INTEGER"
            ))


def _reencode(path_value: str) -> bytes | None:
    path = Path(path_value)
    if not path.is_file():
        return None
    try:
        return pack_embedding(one_face_embedding(path.read_bytes()))
    except (OSError, ValueError):
        return None


def migrate_face_embeddings(engine: Engine) -> None:
    """Rebuild legacy dlib embeddings from the already stored source photos."""
    _ensure_model_columns(engine)
    with Session(engine) as db:
        known_faces = db.scalars(
            select(KnownFace).where(KnownFace.embedding_model != EMBEDDING_MODEL)
        ).all()
        unknown_faces = db.scalars(
            select(UnknownIdentity).where(UnknownIdentity.embedding_model != EMBEDDING_MODEL)
        ).all()

        # SQL NULL does not compare unequal, so include legacy rows explicitly.
        known_faces.extend(db.scalars(
            select(KnownFace).where(KnownFace.embedding_model.is_(None))
        ).all())
        unknown_faces.extend(db.scalars(
            select(UnknownIdentity).where(UnknownIdentity.embedding_model.is_(None))
        ).all())

        for item in {row.id: row for row in known_faces}.values():
            embedding = _reencode(item.image_path)
            if embedding is not None:
                item.embedding = embedding
                item.embedding_model = EMBEDDING_MODEL
        for item in {row.id: row for row in unknown_faces}.values():
            embedding = _reencode(item.image_path)
            if embedding is not None:
                item.embedding = embedding
                item.embedding_model = EMBEDDING_MODEL
        db.commit()

"""Exercise QR pairing and safe camera-device removal through the HTTP API."""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))


def main() -> None:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temporary:
        root = Path(temporary)
        os.environ["DATABASE_URL"] = f"sqlite:///{(root / 'pairing.db').as_posix()}"
        os.environ["STORAGE_DIR"] = str(root / "uploads")

        from fastapi.testclient import TestClient
        from app.main import app

        with TestClient(app) as client:
            registered = client.post("/auth/register", json={
                "display_name": "Pairing Test",
                "email": "pairing@example.com",
                "password": "test-password-123",
            })
            assert registered.status_code == 201, registered.text
            headers = {"Authorization": f"Bearer {registered.json()['access_token']}"}

            created = client.post("/devices/pairing-sessions", headers=headers)
            assert created.status_code == 201, created.text
            pairing = created.json()
            assert "face-recognition-device-pair" in pairing["qr_payload"]

            waiting = client.get(
                f"/devices/pairing-sessions/{pairing['token']}", headers=headers
            )
            assert waiting.status_code == 200 and not waiting.json()["paired"]
            assert waiting.json()["expires_at"].endswith(("Z", "+00:00"))

            paired = client.post("/devices/pair", headers=headers, json={
                "token": pairing["token"],
                "name": "Office Laptop",
            })
            assert paired.status_code == 201, paired.text
            paired_device = paired.json()
            assert paired_device["created_at"].endswith(("Z", "+00:00"))

            complete = client.get(
                f"/devices/pairing-sessions/{pairing['token']}", headers=headers
            )
            assert complete.status_code == 200
            assert complete.json()["device"]["name"] == "Office Laptop"

            removed = client.delete(f"/devices/{paired_device['id']}", headers=headers)
            assert removed.status_code == 204, removed.text
            devices = client.get("/devices", headers=headers).json()
            assert [device["name"] for device in devices] == ["Laptop Camera"]

            removed_default = client.delete(f"/devices/{devices[0]['id']}", headers=headers)
            assert removed_default.status_code == 204, removed_default.text
            assert client.get("/devices", headers=headers).json() == []

    print("QR pairing API smoke test passed")


if __name__ == "__main__":
    main()

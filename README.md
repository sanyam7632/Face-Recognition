# Face Recognition

A privacy-minded CCTV face-recognition system with a Python/FastAPI backend and one Expo React Native client for Android and the web.

## One-click Windows start

Double-click `run.bat` in the project folder. It automatically starts Docker Desktop when needed, builds and starts the backend, installs frontend dependencies, and opens the laptop web application. Keep its terminal window open while using the laptop camera. The Android application connects exactly as before with the same Face Recognition account and backend address.

You can also start it from PowerShell without entering the project path:

```powershell
& "C:\Users\Sanyam Choudhary\Documents\ChatGPT\face recongnition\run.bat"
```

## What it does

- Account registration and JWT login.
- Enrolls a named person from 1–12 single-face reference photos.
- Adds more reference photos and viewing angles to an already enrolled person.
- Deletes a known person together with all stored reference photos.
- Identifies known people from webcam frames.
- Tracks a new face continuously and only records it after two seconds.
- Stores a cropped snapshot and reusable embedding for each unknown identity.
- Matches later sightings to the same `Unknown #ID`.
- Sends the signed-in account an email alert with the snapshot.
- Keeps every account's people, unknown identities, and media isolated.
- Manages multiple named camera devices and lets the viewer show one feed or a multi-camera grid.
- Pairs new laptop cameras with an expiring, single-use QR code scanned by the Android app.
- Removes offline camera devices while preserving face activity history; footage belonging to the removed camera is deleted.
- Records selected camera sources continuously in ten-minute CCTV backup segments, even when nobody is watching the live viewer.
- Shows appearance counts, times, camera names, and recent snapshots for known and unknown people.
- Promotes an unknown identity into the known-people library or deletes known and unknown records.

## Run the backend

The backend uses OpenCV with bundled ONNX models, so Docker is the easiest reproducible path on Windows.

```powershell
Copy-Item .env.example .env
# Edit .env: set SECRET_KEY and, optionally, Gmail SMTP values.
docker compose up --build
```

API documentation is available at `http://localhost:8000/docs` and health at `http://localhost:8000/health`.

For Gmail alerts, enable 2-Step Verification in Google Account, create an App Password, then use that app password as `SMTP_PASSWORD`. If SMTP fields are blank, recognition still works and alerts are simply skipped.

## Run the shared web/Android client

```powershell
Set-Location frontend
npm install
Copy-Item ../.env.example .env
npm run web
```

For a physical Android device, set `EXPO_PUBLIC_API_URL` in `frontend/.env` to `http://YOUR_COMPUTER_LAN_IP:8000`, ensure both devices are on the same network, then run `npm run android`. For production, serve the API over HTTPS.

The login screen also accepts a backend URL. This lets one APK connect to a LAN server during testing or a public HTTPS API in production without rebuilding the app.

## Build a downloadable Android APK

Expo EAS hosts internal APK builds and returns a shareable installation link:

```powershell
Set-Location frontend
npx eas-cli login
npx eas-cli build --platform android --profile preview
```

Sign in when prompted and allow EAS to create the project and Android signing key. The completed build page provides the APK download link. The `preview` profile in `eas.json` produces an APK; the `production` profile produces a Play Store `.aab` instead.

## Recognition flow

1. YuNet detects faces and five facial landmarks in each reference or CCTV image; SFace aligns the face and produces a normalized 128-value embedding.
2. During monitoring, the laptop sends the viewer stream at about 15 FPS and submits a separate recognition frame about every 850 ms.
3. The API combines the closest three reference views for each person, checks the gap from the next-closest identity, and requires the same result in two consecutive recognition frames.
4. A genuinely new face is held in a short-lived per-camera track. After two continuously unmatched seconds it is cropped, assigned a stable unknown ID, persisted, and emailed.
5. Later matches use the same calibrated embedding search and increment that unknown identity's sighting count instead of creating duplicates.

## Laptop camera with phone live viewer

The laptop browser acts as the camera source while the Android app acts as the remote viewer:

1. Start the API on the laptop.
2. In a second PowerShell window run `cd frontend`, `npm install`, then `npm run web`.
3. Open the displayed web address, sign in to the same Face Recognition account, open **Live Feed**, select **Camera source**, choose its device name, allow webcam access, and press play.
4. On Android, sign in to the same account and open **Live Feed**. Choose **One feed** or **Multiple feeds** and select the cameras to watch.

Pair and name camera devices from **Overview** or **Recordings**:

1. On the laptop website, press **Show pairing QR**.
2. On Android, press **Scan laptop QR**, allow camera access, and scan the code.
3. Enter the camera name on Android. The laptop confirms the connection automatically.
4. In the laptop's **Live Feed**, select **Camera source**, choose the paired device, and start the camera.

Both clients must be signed in to the same account and connected to the same backend. Pairing QR codes expire after five minutes and can be used once. Stop a live camera source before removing it. Removing a camera deletes its saved footage but preserves known/unknown face activity.

The laptop sends a low-latency WebSocket stream at about fifteen frames per second and submits a separate frame for recognition about once per second. Android image cross-fading is disabled so continuously changing frames remain bright and crisp. Live frames are relayed in memory. A device with recording enabled also stores ten-minute AVI segments while that camera source is online, regardless of whether the viewer is open. Unknown-person snapshots and reference records continue to use the database. Run one API worker with this in-memory relay and recorder; use Redis or another shared stream broker before scaling to multiple workers.

The client uses a white-and-green interface with **Overview**, **Live Feed**, **People**, **Unknowns**, **Recordings**, and a reserved **Settings** tab. Tapping an unknown snapshot opens it full-screen and provides actions to edit its tag, add it to People, or delete it.

### Watch from outside your local Wi-Fi

An address such as `http://192.168.1.25:8000` is private and only works on the same local network. For remote viewing, use one of these approaches:

- Install Tailscale on the laptop and phone, then use the laptop's Tailscale address as the Android backend URL. This keeps the API private.
- Deploy the backend behind an HTTPS reverse proxy that supports WebSocket upgrades, then enter its `https://` address in both clients. The app automatically uses `wss://` for the live stream.

Do not expose port 8000 directly to the public internet. Use HTTPS/WSS, a strong `SECRET_KEY`, firewall rules, and normal server hardening.

## Recognition accuracy

The backend uses OpenCV Zoo's YuNet detector and SFace recognizer. The matcher was calibrated locally with the Kaggle [Labeled Faces in the Wild dataset](https://www.kaggle.com/datasets/jessicali9530/lfw-dataset). The saved calibration used 1,106 genuine pairs and 7,459 different-person pairs and selected a cosine threshold of `0.346` at a measured false-accept rate of `0.000938` on that sample. These benchmark measurements are not a guarantee for a particular camera, population, distance, or lighting condition.

LFW calibrates the same-person decision boundary; it does not teach the app the people who use it. Each account still learns its own identities only from consented uploads. Existing stored reference photos are automatically re-encoded with SFace when the upgraded backend starts.

For each person, add 3–8 sharp reference photos covering front, slight-left, slight-right, varied expression, and realistic lighting. Avoid duplicates, group photos, filters, sunglasses, heavy blur, and extreme profiles. More low-quality photos can reduce reliability rather than improve it.

To repeat calibration with the locally downloaded dataset, run `python backend/training/calibrate_threshold.py`. The result is saved in `backend/app/model_calibration.json`. `FACE_MATCH_THRESHOLD`, `UNKNOWN_MATCH_THRESHOLD`, `FACE_MATCH_MARGIN`, and `FACE_CONFIRMATION_FRAMES` can override the calibrated defaults after validation with the actual CCTV camera.

## Production checklist

- Use PostgreSQL instead of SQLite for concurrent/multi-instance deployment.
- Put media in encrypted object storage and use expiring signed URLs.
- Run recognition jobs through a queue and rate-limit frame uploads.
- Add account verification, password reset, refresh tokens, audit logs, and retention/deletion controls.
- Use HTTPS, rotate secrets, encrypt backups, and document explicit biometric consent.
- Add liveness/anti-spoofing before using the result for access control. Face recognition alone must not unlock a door or approve a sensitive action.

The app is designed for consensual, private-property monitoring. Check local biometric and surveillance law before deployment.

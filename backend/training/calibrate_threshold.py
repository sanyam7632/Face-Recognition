"""Calibrate the SFace cosine threshold against the Kaggle LFW dataset.

This trains the decision boundary used by this installation; it does not add
the celebrities in LFW to the application's identity database.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


BACKEND_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

from app.face_engine import EMBEDDING_MODEL, one_face_embedding, similarity  # noqa: E402


def identity_folders(root: Path) -> dict[str, list[Path]]:
    identities: dict[str, list[Path]] = {}
    for path in root.rglob("*"):
        if not path.is_dir():
            continue
        images = sorted(path.glob("*.jpg"))
        if images:
            identities[str(path.relative_to(root))] = images
    return identities


def create_pairs(identities: dict[str, list[Path]], genuine_limit: int,
                 impostor_limit: int, seed: int):
    rng = random.Random(seed)
    genuine: list[tuple[Path, Path]] = []
    for images in identities.values():
        if len(images) < 2:
            continue
        shuffled = images[:]
        rng.shuffle(shuffled)
        genuine.extend(zip(shuffled[:-1], shuffled[1:]))
    rng.shuffle(genuine)
    genuine = genuine[:genuine_limit]

    names = list(identities)
    impostor: list[tuple[Path, Path]] = []
    while len(impostor) < impostor_limit and len(names) >= 2:
        left_name, right_name = rng.sample(names, 2)
        impostor.append((rng.choice(identities[left_name]), rng.choice(identities[right_name])))
    return genuine, impostor


def encode_pairs(pairs: list[tuple[Path, Path]], cache: dict[Path, np.ndarray | None]):
    scores: list[float] = []
    for left, right in pairs:
        for path in (left, right):
            if path not in cache:
                try:
                    cache[path] = one_face_embedding(path.read_bytes())
                except (OSError, ValueError):
                    cache[path] = None
        if cache[left] is not None and cache[right] is not None:
            scores.append(similarity(cache[left], cache[right]))
    return np.asarray(scores, dtype=np.float32)


def choose_threshold(genuine: np.ndarray, impostor: np.ndarray, target_far: float):
    thresholds = np.linspace(-0.1, 0.95, 2101)
    false_accept_rates = np.asarray([(impostor >= value).mean() for value in thresholds])
    true_accept_rates = np.asarray([(genuine >= value).mean() for value in thresholds])
    valid = np.flatnonzero(false_accept_rates <= target_far)
    index = int(valid[0]) if len(valid) else len(thresholds) - 1
    return float(thresholds[index]), float(true_accept_rates[index]), float(false_accept_rates[index])


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, default=BACKEND_DIR / "training-data" / "lfw")
    parser.add_argument("--genuine-pairs", type=int, default=1500)
    parser.add_argument("--impostor-pairs", type=int, default=10000)
    parser.add_argument("--target-far", type=float, default=0.001)
    parser.add_argument("--seed", type=int, default=7632)
    parser.add_argument("--output", type=Path, default=BACKEND_DIR / "app" / "model_calibration.json")
    args = parser.parse_args()

    identities = identity_folders(args.dataset)
    if len(identities) < 2:
        raise SystemExit(f"No LFW identity folders found below {args.dataset}")
    genuine_pairs, impostor_pairs = create_pairs(
        identities, args.genuine_pairs, args.impostor_pairs, args.seed
    )
    cache: dict[Path, np.ndarray | None] = {}
    genuine_scores = encode_pairs(genuine_pairs, cache)
    impostor_scores = encode_pairs(impostor_pairs, cache)
    if len(genuine_scores) < 100 or len(impostor_scores) < 100:
        raise SystemExit("Too few usable face pairs were detected to calibrate safely")

    threshold, true_accept_rate, false_accept_rate = choose_threshold(
        genuine_scores, impostor_scores, args.target_far
    )
    result = {
        "embedding_model": EMBEDDING_MODEL,
        "dataset": "Kaggle LFW (jessicali9530/lfw-dataset)",
        "calibrated_at": datetime.now(timezone.utc).isoformat(),
        "cosine_threshold": round(threshold, 4),
        "target_false_accept_rate": args.target_far,
        "measured_false_accept_rate": round(false_accept_rate, 6),
        "measured_true_accept_rate": round(true_accept_rate, 6),
        "genuine_pairs": int(len(genuine_scores)),
        "impostor_pairs": int(len(impostor_scores)),
        "seed": args.seed,
    }
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

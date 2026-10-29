"""Copy the newest Flow download per prompt number into the lesson and build QC contact sheets."""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from cortex.config import CortexConfig
from cortex.lessons.common import LessonError, ffmpeg, probe, sha256_file
from cortex.lessons.spec import LessonSpec

SHEET_FRAMES, SHEET_COLS, SHEET_ROWS, SHEET_WIDTH = 12, 6, 2, 480


def newest_per_number(ingest_dir: Path, numbers: list[str]) -> dict[str, Path]:
    """Flow names a download after its prompt: `07_-_Title_hash.mp4` or `Cena_7_-_Title_hash.mp4`."""
    found: dict[str, Path] = {}
    for num in numbers:
        pattern = re.compile(rf"(?:cena_)?0*{int(num)}_.+\.mp4", re.IGNORECASE)
        candidates = [p for p in ingest_dir.glob("*.mp4") if pattern.fullmatch(p.name)]
        if candidates:
            found[num] = max(candidates, key=lambda p: p.stat().st_mtime)
    return found


def contact_sheet(cfg: CortexConfig, clip: Path, out: Path, duration: float) -> None:
    vf = f"fps={SHEET_FRAMES}/{duration:.4f},scale={SHEET_WIDTH}:-1,tile={SHEET_COLS}x{SHEET_ROWS}"
    ffmpeg(cfg, "-i", clip, "-vf", vf, "-frames:v", 1, "-update", 1, out)


def ingest_lesson(cfg: CortexConfig, directory: Path, spec: LessonSpec, source: Path | None = None) -> dict[str, dict]:
    ingest_dir = Path(source or cfg.lessons.ingest_dir).expanduser()
    numbers = spec.flow_numbers()
    found = newest_per_number(ingest_dir, numbers)
    missing = [n for n in numbers if n not in found]
    if missing:
        raise LessonError(f"no download in {ingest_dir} for Flow clip(s) {missing}")
    flow_dir, check_dir = directory / "flow", directory / "check"
    flow_dir.mkdir(exist_ok=True)
    check_dir.mkdir(exist_ok=True)
    record: dict[str, dict] = {}
    for num, source in found.items():
        dest = flow_dir / f"{num}.mp4"
        digest = sha256_file(source)
        if not dest.exists() or sha256_file(dest) != digest:
            shutil.copy2(source, dest)
        info = probe(cfg, dest)
        contact_sheet(cfg, dest, check_dir / f"flow_{num}.png", info.duration)
        record[num] = {"source": source.name, "sha256": digest, "duration": round(info.duration, 3),
                       "width": info.width, "height": info.height, "fps": round(info.fps, 3)}
    (directory / "ingest.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record

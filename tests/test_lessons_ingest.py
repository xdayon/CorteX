from __future__ import annotations

import json
import os

from lessons_media import ff, small_config

from cortex.lessons.ingest import ingest_lesson
from cortex.lessons.spec import Beat, FlowRef, LessonSpec, Shot


def test_newest_wins_only_referenced_numbers_and_qc_sheet(tmp_path):
    cfg = small_config(tmp_path)
    ingest = tmp_path / "ingest"
    ingest.mkdir()
    old, new, other = ingest / "02_-_old_aaaa.mp4", ingest / "02_-_new_bbbb.mp4", ingest / "09_-_unused_cccc.mp4"
    for path, color in ((old, "red"), (new, "blue"), (other, "green")):
        ff("-f", "lavfi", "-i", f"color={color}:s=160x90:r=10:d=2", "-pix_fmt", "yuv420p", path)
    os.utime(old, (1_000, 1_000))
    os.utime(new, (2_000, 2_000))
    lesson = tmp_path / "lesson"
    lesson.mkdir()
    spec = LessonSpec(slug="t", title="t", narration="n.mp3", beats=[Beat(id="B1", text="x")],
                      shots=[Shot(id="A", beats=["B1"], kind="flow", flow=FlowRef(clip="02"))])
    record = ingest_lesson(cfg, lesson, spec)
    assert list(record) == ["02"]
    assert record["02"]["source"] == new.name
    assert (lesson / "flow" / "02.mp4").read_bytes() == new.read_bytes()
    assert not (lesson / "flow" / "09.mp4").exists()
    assert (lesson / "check" / "flow_02.png").stat().st_size > 0
    assert json.loads((lesson / "ingest.json").read_text())["02"]["sha256"] == record["02"]["sha256"]

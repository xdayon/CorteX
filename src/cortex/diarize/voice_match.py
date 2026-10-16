"""Fast target-speaker labeling: a stored voiceprint of the protagonist scored per selection unit.

Replaces running full diarization for new episodes. The torch work happens only in
`voice_runner.py` (optional CPU env); this module stays free of torch.

Output labels: the protagonist is `SUBJECT_LABEL` ("protagonista") and everyone else
`OTHER_LABEL` ("outros"); the result is an ordinary diarization artifact (engine
"voice-match-1") so the confirmed-voice plumbing downstream is unchanged.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from cortex.config import CortexConfig
from cortex.diarize.service import DiarizationDocument, VoiceTurn, runtime_path
from cortex.domain.models import StageArtifact
from cortex.ingest.normalize import extract_normalized_audio
from cortex.paths import cache_dir
from cortex.schemas import utc_now

PROFILE_SCHEMA_VERSION = 1
ENGINE = "voice-match-1"
SUBJECT_LABEL = "protagonista"
OTHER_LABEL = "outros"
SHORT_UNIT_SECONDS = 0.8
MAX_PROFILE_TURNS = 40
MIN_PROFILE_TURN_SECONDS = 3.0
PROFILE_CROP_SECONDS = 6.0
PROFILE_BUILD_TIMEOUT_SECONDS = 600
CALIBRATION_MAX_UNITS_PER_CLASS = 400
CALIBRATION_MIN_UNITS_PER_CLASS = 50
CALIBRATION_COVERAGE = 0.8
THRESHOLD_BOUNDS = (0.25, 0.60)


class VoiceMatchCancelled(RuntimeError):
    pass


class VoiceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = PROFILE_SCHEMA_VERSION
    created_at: datetime = Field(default_factory=utc_now)
    embedding: list[float] = Field(min_length=1)
    threshold: float
    model_repo: str
    model_revision: str
    source: dict[str, Any]
    calibration: dict[str, Any] = Field(default_factory=dict)

    def content_hash(self) -> str:
        return hashlib.sha256(json.dumps(
            {"embedding": self.embedding, "model": [self.model_repo, self.model_revision]},
            separators=(",", ":"),
        ).encode()).hexdigest()


def profile_path(config: CortexConfig) -> Path:
    return config.paths.data_dir / "voice" / "profile.json"


def load_profile(config: CortexConfig) -> VoiceProfile | None:
    path = profile_path(config)
    if not path.is_file():
        return None
    return VoiceProfile.model_validate_json(path.read_text())


def save_profile(config: CortexConfig, profile: VoiceProfile) -> Path:
    path = profile_path(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(profile.model_dump_json())
    tmp.replace(path)
    return path


def runner_path() -> Path:
    return Path(__file__).with_name("voice_runner.py")


def run_voice_runner(
    config: CortexConfig, command: str, *, audio: Path, spans: list[tuple[float, float]],
    workdir: Path, stem: str, profile: Path | None = None, timeout: float,
    progress_cb: Callable[[float, str], None], should_cancel: Callable[[], bool],
) -> dict[str, Any]:
    """Run one runner subcommand in the torch env; the only subprocess boundary."""
    runtime = runtime_path()
    if not runtime.is_file():
        raise ValueError("Instale o ambiente de vozes em CPU: scripts/setup_diarization.sh")
    workdir.mkdir(parents=True, exist_ok=True)
    spans_file = workdir / f"{stem}.spans.json"
    output = workdir / f"{stem}.{command}.json"
    progress = workdir / f"{stem}.progress.json"
    spans_file.write_text(json.dumps([[a, b] for a, b in spans]))
    argv = [str(runtime), str(runner_path()), command, str(audio), str(spans_file), str(output),
            str(progress), "--threads", str(config.voice.max_threads)]
    if profile is not None:
        argv += ["--profile", str(profile)]
    env = {**os.environ, "HF_HUB_OFFLINE": "1"}
    started = time.monotonic()
    process = subprocess.Popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, env=env)
    try:
        while process.poll() is None:
            if should_cancel():
                raise VoiceMatchCancelled()
            if time.monotonic() - started > timeout:
                raise TimeoutError(
                    f"O reconhecimento de voz excedeu {int(timeout)}s; nenhum resultado parcial foi usado"
                )
            try:
                state = json.loads(progress.read_text())
                total = state.get("total") or 0
                done = state.get("completed") or 0
                progress_cb(5 + 90 * (done / total if total else 0.0),
                            f"Reconhecendo a voz do Dayon · {done}/{total} trechos")
            except (OSError, ValueError):
                pass
            time.sleep(1)
        if process.returncode:
            detail = (process.stderr.read() or b"").decode(errors="replace")[-400:] if process.stderr else ""
            raise RuntimeError(
                "O reconhecimento de voz falhou; confira o modelo community-1 no cache local e a "
                f"memória disponível. {detail}".strip()
            )
        return json.loads(output.read_text())
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        for leftover in (spans_file, output, progress):
            leftover.unlink(missing_ok=True)


# --- profile -------------------------------------------------------------------------------


def _spread(items: list, limit: int) -> list:
    if len(items) <= limit:
        return list(items)
    return [items[round(i * (len(items) - 1) / (limit - 1))] for i in range(limit)]


def clean_turns(
    turns: list[VoiceTurn], speaker: str, *, subject: bool,
) -> list[tuple[float, float]]:
    """Turns of the subject (or of anyone else) lasting >= 3 s and never overlapping another voice."""
    ranges = []
    for turn in turns:
        if (turn.speaker == speaker) != subject or turn.end - turn.start < MIN_PROFILE_TURN_SECONDS:
            continue
        competing = [t for t in turns if t.speaker != turn.speaker and t.start < turn.end and t.end > turn.start]
        if not competing:
            ranges.append((turn.start, min(turn.end, turn.start + PROFILE_CROP_SECONDS)))
    return ranges


def calibrate_threshold(
    positive: list[float], negative: list[float], default: float,
) -> float:
    """Threshold maximizing balanced accuracy; the middle of the best plateau."""
    if not positive or not negative:
        return default
    values = sorted(set(positive) | set(negative))
    if len(values) < 2:
        return default
    candidates = [(a + b) / 2 for a, b in zip(values, values[1:])]

    def balanced(threshold: float) -> float:
        recall = sum(s >= threshold for s in positive) / len(positive)
        specificity = sum(s < threshold for s in negative) / len(negative)
        return (recall + specificity) / 2

    best = max(balanced(c) for c in candidates)
    plateau = [c for c in candidates if balanced(c) >= best - 1e-9]
    return float(plateau[len(plateau) // 2])


def balanced_accuracy(positive: list[float], negative: list[float], threshold: float) -> float:
    recall = sum(s >= threshold for s in positive) / len(positive)
    specificity = sum(s < threshold for s in negative) / len(negative)
    return (recall + specificity) / 2


def _unit_truth(
    spans: list[tuple[float, float]], turns: list[VoiceTurn], speaker: str,
) -> list[bool | None]:
    """True/False when one diarized voice covers >= 80% of the unit; None when ambiguous."""
    result: list[bool | None] = []
    for start, end in spans:
        coverage: dict[str, float] = {}
        for turn in turns:
            shared = min(end, turn.end) - max(start, turn.start)
            if shared > 0:
                coverage[turn.speaker] = coverage.get(turn.speaker, 0.0) + shared
        best = max(coverage, key=lambda k: coverage[k]) if coverage else None
        rest = max((v for k, v in coverage.items() if k != best), default=0.0)
        if (best is None or coverage[best] / (end - start) < CALIBRATION_COVERAGE
                or rest / (end - start) >= 1 - CALIBRATION_COVERAGE):
            result.append(None)
        else:
            result.append(best == speaker)
    return result


def _calibration_units(
    spans: list[tuple[float, float]], turns: list[VoiceTurn], speaker: str,
    exclude: list[tuple[float, float]],
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Subsampled (subject, other) selection units, spread over the episode, away from the voiceprint crops."""
    usable = [
        (i, span) for i, span in enumerate(spans)
        if span[1] - span[0] >= SHORT_UNIT_SECONDS
        and not any(span[0] < b and span[1] > a for a, b in exclude)
    ]
    truth = _unit_truth([span for _, span in usable], turns, speaker)
    subject = [span for (_, span), t in zip(usable, truth) if t is True]
    other = [span for (_, span), t in zip(usable, truth) if t is False]
    return (_spread(subject, CALIBRATION_MAX_UNITS_PER_CLASS),
            _spread(other, CALIBRATION_MAX_UNITS_PER_CLASS))


def _unit(vector: np.ndarray) -> np.ndarray:
    return vector / np.linalg.norm(vector)


def profile_from_embeddings(
    positives: np.ndarray, negatives: np.ndarray, default_threshold: float,
) -> tuple[np.ndarray, float]:
    """Voiceprint = normalized mean of the positives; threshold from leave-one-out scores."""
    total = positives.sum(axis=0)
    voiceprint = _unit(total)
    if len(positives) > 1:
        positive_scores = [float(_unit(total - e) @ e) for e in positives]
    else:
        positive_scores = [float(voiceprint @ positives[0])]
    negative_scores = [float(voiceprint @ e) for e in negatives]
    return voiceprint, calibrate_threshold(positive_scores, negative_scores, default_threshold)


def build_profile_from_episode(
    config: CortexConfig, domain, entry, *,
    progress_cb: Callable[[float, str], None] = lambda _p, _m: None,
    should_cancel: Callable[[], bool] = lambda: False,
    before: float | None = None,
) -> VoiceProfile:
    reference = entry.subject_reference or {}
    artifact_id, speaker = reference.get("diarization_artifact_id"), reference.get("speaker")
    if not artifact_id or not speaker:
        raise LookupError("O episódio não tem uma voz confirmada")
    artifact = domain.get_stage_artifact(artifact_id)
    if artifact.stage != "diarization" or artifact.project_id not in entry.project_ids:
        raise ValueError("Vozes não pertencem ao episódio")
    document = DiarizationDocument.model_validate_json(Path(artifact.path).read_text())
    turns = [t for t in document.turns if before is None or t.end <= before]
    positives = _spread(clean_turns(turns, speaker, subject=True), MAX_PROFILE_TURNS)
    negatives = _spread(clean_turns(turns, speaker, subject=False), MAX_PROFILE_TURNS)
    if not positives:
        raise ValueError("Nenhum turno limpo (>= 3 s, sem sobreposição) da voz confirmada")
    source = domain.get_source_asset(artifact.metadata["source_asset_id"])
    audio = Path(extract_normalized_audio(
        config.render.ffmpeg, config.render.ffprobe, config.paths.data_dir / source.stored_path,
        source_sha256=source.sha256, cache_dir=cache_dir(config, source.project_id),
    ).path)
    workdir = cache_dir(config, source.project_id) / "diarization"
    result = run_voice_runner(
        config, "embed", audio=audio, spans=[*positives, *negatives],
        workdir=workdir, stem=f"profile-{artifact.id}",
        timeout=PROFILE_BUILD_TIMEOUT_SECONDS,
        progress_cb=lambda p, m: progress_cb(p * 0.3, m), should_cancel=should_cancel,
    )
    embeddings = result["embeddings"]
    pos = np.array([e for e in embeddings[:len(positives)] if e is not None])
    neg = np.array([e for e in embeddings[len(positives):] if e is not None])
    if not len(pos):
        raise ValueError("Não foi possível extrair a voz dos turnos limpos")
    voiceprint, threshold = profile_from_embeddings(
        pos, neg if len(neg) else np.empty((0, pos.shape[1])), config.voice.default_threshold,
    )
    calibration: dict[str, Any] = {
        "method": "turns" if len(neg) else "default", "n_pos": len(pos), "n_neg": len(neg),
    }
    if len(neg):
        calibration["balanced_accuracy"] = round(balanced_accuracy(
            [float(_unit(np.sum(pos, axis=0) - e) @ e) if len(pos) > 1 else float(voiceprint @ e) for e in pos],
            [float(voiceprint @ e) for e in neg], threshold), 4)
        unit_threshold = _calibrate_on_units(
            config, domain, source, audio, workdir, voiceprint, turns, speaker, positives,
            before, calibration, progress_cb, should_cancel, artifact_id=artifact.id,
        )
        if unit_threshold is not None:
            threshold = unit_threshold
    profile = VoiceProfile(
        embedding=[float(x) for x in voiceprint], threshold=threshold,
        model_repo=result["model_repo"], model_revision=result["model_revision"],
        calibration=calibration,
        source={
            "episode_id": entry.id, "diarization_artifact_id": artifact.id, "speaker": speaker,
            "positive_ranges": [[a, b] for a, b in positives],
            "negative_ranges": [[a, b] for a, b in negatives],
            "seconds": round(sum(b - a for a, b in positives), 2),
        },
    )
    return profile


def _calibrate_on_units(
    config, domain, source, audio, workdir, voiceprint, turns, speaker, positives, before,
    calibration, progress_cb, should_cancel, *, artifact_id,
) -> float | None:
    """Threshold from selection units of the enrollment episode (the domain where it is applied)."""
    from cortex.suggest.service import selection_unit_spans

    transcripts = domain.list_transcript_artifacts(source.id)
    if not transcripts:
        return None
    transcript = json.loads(Path(transcripts[-1].path).read_text(encoding="utf-8"))
    spans = [s for s in selection_unit_spans(transcript) if before is None or s[1] <= before]
    subject_units, other_units = _calibration_units(spans, turns, speaker, positives)
    if min(len(subject_units), len(other_units)) < CALIBRATION_MIN_UNITS_PER_CLASS:
        return None
    temp_profile = workdir / f"profile-{artifact_id}.voiceprint.json"
    temp_profile.parent.mkdir(parents=True, exist_ok=True)
    temp_profile.write_text(json.dumps({"embedding": [float(x) for x in voiceprint]}))
    try:
        scored = run_voice_runner(
            config, "score", audio=audio, spans=[*subject_units, *other_units], workdir=workdir,
            stem=f"profile-{artifact_id}-units", profile=temp_profile,
            timeout=PROFILE_BUILD_TIMEOUT_SECONDS,
            progress_cb=lambda p, m: progress_cb(30 + p * 0.7, m), should_cancel=should_cancel,
        )
    finally:
        temp_profile.unlink(missing_ok=True)
    scores = scored["scores"]
    pos_scores = [x for x in scores[:len(subject_units)] if x is not None]
    neg_scores = [x for x in scores[len(subject_units):] if x is not None]
    if min(len(pos_scores), len(neg_scores)) < CALIBRATION_MIN_UNITS_PER_CLASS:
        return None
    raw = calibrate_threshold(pos_scores, neg_scores, config.voice.default_threshold)
    threshold = min(max(raw, THRESHOLD_BOUNDS[0]), THRESHOLD_BOUNDS[1])
    calibration.update({
        "method": "selection_units", "n_pos": len(pos_scores), "n_neg": len(neg_scores),
        "balanced_accuracy": round(balanced_accuracy(pos_scores, neg_scores, threshold), 4),
        "unclamped_threshold": round(raw, 4),
    })
    return threshold


# --- matching ------------------------------------------------------------------------------


def label_units(
    spans: list[tuple[float, float]], scores: list[float | None], threshold: float,
) -> list[bool]:
    """True when the unit is the protagonist. Units under 0.8 s (or unscored) copy the nearest reliable unit."""
    reliable = [
        i for i, (span, score) in enumerate(zip(spans, scores))
        if score is not None and span[1] - span[0] >= SHORT_UNIT_SECONDS
    ]
    if not reliable:
        raise ValueError("Nenhum trecho longo o bastante para reconhecer a voz")
    labels = [score is not None and score >= threshold for score in scores]
    middles = [(a + b) / 2 for a, b in spans]
    trusted = set(reliable)
    for i in range(len(spans)):
        if i not in trusted:
            nearest = min(reliable, key=lambda j: abs(middles[j] - middles[i]))
            labels[i] = labels[nearest]
    return labels


def merge_turns(spans: list[tuple[float, float]], labels: list[bool]) -> list[VoiceTurn]:
    turns: list[VoiceTurn] = []
    for (start, end), is_subject in zip(spans, labels):
        speaker = SUBJECT_LABEL if is_subject else OTHER_LABEL
        if turns and turns[-1].speaker == speaker:
            turns[-1] = VoiceTurn(start=turns[-1].start, end=end, speaker=speaker)
        else:
            turns.append(VoiceTurn(start=start, end=end, speaker=speaker))
    return turns


def match_transcript(
    config: CortexConfig, domain, *, source, transcript_artifact, profile: VoiceProfile,
    progress_cb: Callable[[float, str], None], should_cancel: Callable[[], bool],
) -> dict[str, Any]:
    """Label the transcript with the voiceprint; returns the diarization artifact id and speaker label."""
    from cortex.suggest.service import selection_unit_spans

    transcript = json.loads(Path(transcript_artifact.path).read_text(encoding="utf-8"))
    spans = selection_unit_spans(transcript)
    if not spans:
        raise ValueError("Transcrição sem trechos para reconhecer a voz")
    profile_hash = profile.content_hash()
    key = hashlib.sha256(json.dumps({
        "source": source.sha256, "transcript": transcript_artifact.id,
        "transcript_audio": transcript_artifact.audio_sha256, "profile": profile_hash,
        "threshold": round(profile.threshold, 6),
        "runner": hashlib.sha256(runner_path().read_bytes()).hexdigest(),
        "spans": hashlib.sha256(json.dumps(spans).encode()).hexdigest(),
        "version": 1,
    }, sort_keys=True).encode()).hexdigest()
    cached = domain.find_cached_stage_artifact(
        project_id=source.project_id, stage="diarization", input_hash=key, schema_version=1,
    )
    if cached and Path(cached.path).is_file():
        DiarizationDocument.model_validate_json(Path(cached.path).read_text())
        return {"diarization_artifact_id": cached.id, "speaker": SUBJECT_LABEL,
                "threshold": profile.threshold, "cached": True}
    if should_cancel():
        raise VoiceMatchCancelled()
    started = time.monotonic()
    audio = extract_normalized_audio(
        config.render.ffmpeg, config.render.ffprobe, config.paths.data_dir / source.stored_path,
        source_sha256=source.sha256, cache_dir=cache_dir(config, source.project_id),
    )
    directory = cache_dir(config, source.project_id) / "diarization"
    result = run_voice_runner(
        config, "score", audio=Path(audio.path), spans=spans, workdir=directory, stem=key,
        profile=profile_path(config), timeout=config.voice.timeout_seconds,
        progress_cb=progress_cb, should_cancel=should_cancel,
    )
    scores = result["scores"]
    if len(scores) != len(spans):
        raise RuntimeError("O reconhecimento de voz devolveu uma quantidade inesperada de trechos")
    labels = label_units(spans, scores, profile.threshold)
    turns = merge_turns(spans, labels)
    if not any(t.speaker == SUBJECT_LABEL for t in turns):
        raise ValueError("A voz do Dayon não foi reconhecida neste episódio")
    elapsed = time.monotonic() - started
    document = DiarizationDocument(
        engine=ENGINE, engine_version=str(result["model_revision"])[:12],
        requested_device="cpu", effective_device="cpu", elapsed_seconds=elapsed, turns=turns,
    )
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / f"{key}.json"
    tmp = output.with_suffix(".tmp")
    tmp.write_text(document.model_dump_json())
    tmp.replace(output)
    artifact = domain.create_stage_artifact(StageArtifact(
        project_id=source.project_id, stage="diarization", schema_version=1, path=str(output),
        input_hash=key,
        metadata={
            "source_asset_id": source.id, "source_sha256": source.sha256, "method": "voice_match",
            "profile_hash": profile_hash, "threshold": profile.threshold, "effective_device": "cpu",
            "subject_speaker": SUBJECT_LABEL, "units": len(spans), "elapsed_seconds": elapsed,
        },
    ))
    progress_cb(100, "Voz do Dayon reconhecida")
    return {"diarization_artifact_id": artifact.id, "speaker": SUBJECT_LABEL,
            "threshold": profile.threshold, "cached": False}

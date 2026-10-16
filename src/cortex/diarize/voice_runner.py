"""Executed with the optional CPU Python environment (torch + pyannote.audio).

Subcommands:
  score AUDIO UNITS OUTPUT PROGRESS --profile PROFILE   cosine score of each [start, end] unit
  embed AUDIO RANGES OUTPUT PROGRESS                    mean + per-range normalized embeddings

The WeSpeaker embedding model is loaded offline from the local Hugging Face cache.
"""
import argparse
import json
import os
import time
import wave
from pathlib import Path

SAMPLE_RATE = 16000
MODEL_REPO = "pyannote/speaker-diarization-community-1"
MIN_UNIT_SECONDS = 0.3
LONG_UNIT_SECONDS = 6.0
WINDOW_SECONDS = 3.0
TAIL_SECONDS = 0.5


def write_json(path, document):
    target = Path(path)
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(document))
    tmp.replace(target)


def write_progress(path, step, total, completed):
    write_json(path, {"step": step, "total": int(total), "completed": int(completed)})


def crops_for(start, end, *, crop_long):
    """One crop up to 6 s; longer units use 3 s windows (hop 3 s) or, for ranges, the first 6 s."""
    if end - start <= LONG_UNIT_SECONDS:
        return [(start, end)]
    if crop_long:
        return [(start, start + LONG_UNIT_SECONDS)]
    crops, cursor = [], start
    while cursor + WINDOW_SECONDS <= end:
        crops.append((cursor, cursor + WINDOW_SECONDS))
        cursor += WINDOW_SECONDS
    if end - cursor > TAIL_SECONDS:
        crops.append((end - WINDOW_SECONDS, end))
    return crops


def load_model():
    os.environ.setdefault("HF_HUB_OFFLINE", "1")
    import torch
    from huggingface_hub import snapshot_download
    from pyannote.audio import Model

    snapshot = Path(snapshot_download(MODEL_REPO, local_files_only=True))
    model = Model.from_pretrained(str(snapshot / "embedding")).eval()
    return torch, model, snapshot.name


def read_waveform(torch, path):
    import numpy as np

    with wave.open(path, "rb") as audio:
        if audio.getnchannels() != 1 or audio.getframerate() != SAMPLE_RATE or audio.getsampwidth() != 2:
            raise ValueError("Expected normalized mono 16 kHz PCM16")
        pcm = np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2")
    return torch.from_numpy(pcm.astype("float32") / 32768)


def embed_spans(torch, model, waveform, spans, *, crop_long, progress, label):
    """Normalized embedding per span (None when too short); windows are averaged then renormalized."""
    import numpy as np

    results = []
    with torch.inference_mode():
        for index, (start, end) in enumerate(spans):
            if end - start < MIN_UNIT_SECONDS:
                results.append(None)
            else:
                vectors = []
                for a, b in crops_for(start, end, crop_long=crop_long):
                    chunk = waveform[max(0, int(a * SAMPLE_RATE)): int(b * SAMPLE_RATE)]
                    if chunk.numel() < int(MIN_UNIT_SECONDS * SAMPLE_RATE):
                        continue
                    vector = model(chunk[None, None, :]).cpu().numpy()[0]
                    vectors.append(vector / np.linalg.norm(vector))
                if vectors:
                    mean = np.mean(vectors, axis=0)
                    results.append(mean / np.linalg.norm(mean))
                else:
                    results.append(None)
            if index % 25 == 0:
                progress(label, len(spans), index)
    progress(label, len(spans), len(spans))
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["score", "embed"])
    parser.add_argument("audio")
    parser.add_argument("spans")
    parser.add_argument("output")
    parser.add_argument("progress")
    parser.add_argument("--profile")
    parser.add_argument("--threads", type=int, default=8)
    args = parser.parse_args()
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    started = time.monotonic()
    torch, model, revision = load_model()
    torch.set_num_threads(max(1, min(args.threads, os.cpu_count() or 1)))
    import numpy as np

    waveform = read_waveform(torch, args.audio)
    spans = [(float(a), float(b)) for a, b in json.loads(Path(args.spans).read_text())]
    progress = lambda step, total, done: write_progress(args.progress, step, total, done)  # noqa: E731
    base = {"model_repo": MODEL_REPO, "model_revision": revision}
    if args.command == "score":
        voiceprint = np.asarray(json.loads(Path(args.profile).read_text())["embedding"], dtype="float32")
        voiceprint = voiceprint / np.linalg.norm(voiceprint)
        embeddings = embed_spans(torch, model, waveform, spans, crop_long=False, progress=progress, label="scoring")
        scores = [None if e is None else float(np.dot(e, voiceprint)) for e in embeddings]
        document = {**base, "scores": scores}
    else:
        embeddings = embed_spans(torch, model, waveform, spans, crop_long=True, progress=progress, label="embedding")
        valid = [e for e in embeddings if e is not None]
        if not valid:
            raise ValueError("No usable range to embed")
        mean = np.mean(valid, axis=0)
        document = {**base, "mean": (mean / np.linalg.norm(mean)).tolist(),
                    "embeddings": [None if e is None else e.tolist() for e in embeddings]}
    document["elapsed_seconds"] = time.monotonic() - started
    write_json(args.output, document)


if __name__ == "__main__":
    main()

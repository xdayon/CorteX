"""Executed with the optional CPU Python environment; secrets are never CLI arguments."""
import argparse
import json
import os
import time
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("audio")
    parser.add_argument("output")
    parser.add_argument("progress")
    parser.add_argument("--speakers", type=int)
    args = parser.parse_args()
    os.environ["PYANNOTE_METRICS_ENABLED"] = "0"
    import numpy as np
    import torch
    import wave
    from importlib.metadata import version
    from pyannote.audio import Pipeline

    torch.set_num_threads(4)
    started = time.monotonic()
    pipeline = Pipeline.from_pretrained("pyannote/speaker-diarization-community-1", token=os.environ.get("HF_TOKEN"))
    pipeline.to(torch.device("cpu"))
    with wave.open(args.audio, "rb") as audio:
        if audio.getnchannels() != 1 or audio.getframerate() != 16000 or audio.getsampwidth() != 2:
            raise ValueError("Expected normalized mono 16 kHz PCM16")
        waveform = torch.from_numpy(np.frombuffer(audio.readframes(audio.getnframes()), dtype="<i2").astype("float32") / 32768).unsqueeze(0)
    def hook(step_name, step_artifact, file=None, total=None, completed=None):
        del step_artifact, file
        target = Path(args.progress)
        tmp = target.with_suffix(".tmp")
        tmp.write_text(json.dumps({"step":str(step_name), "total":total, "completed":completed}))
        tmp.replace(target)
    output = pipeline({"waveform":waveform,"sample_rate":16000}, hook=hook, **({"num_speakers":args.speakers} if args.speakers else {}))
    turns = [{"start":turn.start,"end":turn.end,"speaker":speaker} for turn, speaker in output.speaker_diarization]
    document = {"schema_version":1,"engine":"pyannote-community-1","engine_version":version("pyannote.audio"),"requested_device":"cpu","effective_device":"cpu","elapsed_seconds":time.monotonic()-started,"turns":turns}
    target = Path(args.output)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(document))
    tmp.replace(target)


if __name__ == "__main__":
    main()

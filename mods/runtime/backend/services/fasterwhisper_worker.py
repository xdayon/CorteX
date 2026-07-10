#!/usr/bin/env python3
"""faster-whisper worker — runs INSIDE the CUDA venv (not the podcli runtime).

Invoked by transcription_fasterwhisper.py via subprocess using the venv's python,
so the podcli runtime python stays free of torch/ctranslate2. Emits a JSON blob on
stdout in the exact shape transcribe_file() expects (segments + word timings).

Self-contained on purpose: imports only faster_whisper + stdlib.
"""
import argparse
import json
import sys


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("audio")
    p.add_argument("--model", default="large-v3")
    p.add_argument("--language", default=None)
    p.add_argument("--device", default="cuda")
    p.add_argument("--compute-type", default="int8")
    p.add_argument("--beam-size", type=int, default=5)
    p.add_argument("--no-vad", action="store_true")
    p.add_argument("--initial-prompt", default=None)
    p.add_argument("--batch-size", type=int, default=8,
                   help="0 disables batching (sequential decode)")
    args = p.parse_args()

    from faster_whisper import WhisperModel

    device, compute = args.device, args.compute_type
    try:
        model = WhisperModel(args.model, device=device, compute_type=compute)
    except Exception as e:  # GPU unavailable / OOM / unsupported compute type
        sys.stderr.write(f"[fasterwhisper] load on {device}/{compute} failed ({e}); using cpu/int8\n")
        device, compute = "cpu", "int8"
        model = WhisperModel(args.model, device="cpu", compute_type="int8")

    common = dict(
        language=args.language,
        beam_size=args.beam_size,
        vad_filter=not args.no_vad,
        word_timestamps=True,
        condition_on_previous_text=False,
        initial_prompt=args.initial_prompt,
    )

    # Keep the actual Silero speech regions for the editing engine. Whisper word
    # timestamps alone are not a safe waveform boundary: low-confidence or
    # untranscribed syllables can still contain voice.
    speech_intervals = []
    if not args.no_vad:
        try:
            from faster_whisper.audio import decode_audio
            from faster_whisper.vad import VadOptions, get_speech_timestamps

            decoded = decode_audio(args.audio)
            chunks = get_speech_timestamps(
                decoded,
                VadOptions(min_silence_duration_ms=250, speech_pad_ms=80),
            )
            speech_intervals = [
                {
                    "start": round(float(chunk["start"]) / 16000.0, 3),
                    "end": round(float(chunk["end"]) / 16000.0, 3),
                }
                for chunk in chunks
                if chunk.get("end", 0) > chunk.get("start", 0)
            ]
        except Exception as e:
            sys.stderr.write(f"[fasterwhisper] VAD timeline unavailable ({e})\n")

    # Batched decode (~3-4x on GPU): VAD splits speech into chunks decoded in
    # parallel. transcribe() is lazy — OOM surfaces while iterating — so the
    # segments are materialized inside the try; falls back to sequential.
    seg_list = None
    if args.batch_size > 0 and device == "cuda":
        try:
            from faster_whisper import BatchedInferencePipeline
            batched = BatchedInferencePipeline(model=model)
            seg_iter, info = batched.transcribe(
                args.audio, batch_size=args.batch_size, **common,
            )
            seg_list = list(seg_iter)
        except Exception as e:
            sys.stderr.write(f"[fasterwhisper] batched decode failed ({e}); sequential\n")
            seg_list = None

    if seg_list is None:
        seg_iter, info = model.transcribe(args.audio, **common)
        seg_list = list(seg_iter)
    segments_iter = seg_list

    segments, words = [], []
    for i, seg in enumerate(segments_iter):
        segments.append({
            "id": i,
            "start": round(seg.start, 3),
            "end": round(seg.end, 3),
            "text": (seg.text or "").strip(),
            "speaker": None,
        })
        for w in (seg.words or []):
            txt = (w.word or "").strip()
            if not txt:
                continue
            words.append({
                "word": txt,
                "start": round(w.start, 3),
                "end": round(w.end, 3),
                "confidence": round((getattr(w, "probability", 0.0) or 0.0), 3),
                "speaker": None,
            })

    duration = round(
        (getattr(info, "duration", 0.0) or (segments[-1]["end"] if segments else 0.0)), 3
    )
    out = {
        "transcript": " ".join(s["text"] for s in segments).strip(),
        "segments": segments,
        "words": words,
        "duration": duration,
        "language": getattr(info, "language", None) or args.language or "pt",
        "device": device,
        "compute_type": compute,
        "speech_intervals": speech_intervals,
    }
    json.dump(out, sys.stdout, ensure_ascii=False)


if __name__ == "__main__":
    main()

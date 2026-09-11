from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cortex.domain.models import StageArtifact
from cortex.ingest.normalize import extract_normalized_audio
from cortex.paths import cache_dir


class VoiceTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    speaker: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("Intervalo de fala inválido")
        return self


class DiarizationDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    schema_version: int = 1
    engine: str
    engine_version: str
    requested_device: str
    effective_device: str
    elapsed_seconds: float = Field(ge=0)
    turns: list[VoiceTurn]


def runtime_path():
    return Path(os.environ.get("CORTEX_DIARIZATION_PYTHON", ".venv-diarization/bin/python")).resolve()


def readiness():
    return {"runtime_installed":runtime_path().is_file(), "token_configured":bool(os.environ.get("HF_TOKEN")), "device":"cpu"}


def run_diarization(config, domain, source, speakers, progress_cb, should_cancel):
    runtime = runtime_path()
    if not runtime.is_file():
        raise ValueError("Instale o ambiente CPU: scripts/setup_diarization.sh")
    # Include the isolated runtime installation and runner bytes in the deterministic cache key.
    runner = Path(__file__).with_name("runner.py")
    versions = subprocess.run([str(runtime), "-c", "from importlib.metadata import version; print(version('pyannote.audio'), version('torch'))"], capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    key = hashlib.sha256(json.dumps({"source":source.sha256,"speakers":speakers,"device":"cpu","versions":versions,"runner":hashlib.sha256(runner.read_bytes()).hexdigest()},sort_keys=True).encode()).hexdigest()
    cached = domain.find_cached_stage_artifact(project_id=source.project_id, stage="diarization", input_hash=key, schema_version=1)
    if cached:
        DiarizationDocument.model_validate_json(Path(cached.path).read_text())
        return {"diarization_artifact_id":cached.id,"cached":True}
    if not readiness()["token_configured"]:
        raise ValueError("Configure HF_TOKEN localmente e aceite o acesso ao modelo pyannote community-1 no Hugging Face.")
    if should_cancel():
        return None
    progress_cb(1, "Preparando áudio para separar vozes em CPU")
    audio = extract_normalized_audio(config.render.ffmpeg, config.render.ffprobe, config.paths.data_dir/source.stored_path, source_sha256=source.sha256, cache_dir=cache_dir(config, source.project_id))
    directory = cache_dir(config, source.project_id) / "diarization"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory/f"{key}.json"
    progress = directory/f"{key}.progress.json"
    command = [str(runtime),str(runner),str(audio.path),str(output),str(progress)]
    if speakers:
        command += ["--speakers",str(speakers)]
    progress_cb(5, "Carregando pyannote em CPU; primeira execução baixa o modelo")
    started = time.monotonic()
    process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        while process.poll() is None:
            if should_cancel():
                return None
            if time.monotonic()-started > 8*3600:
                raise TimeoutError("Diarização excedeu 8 horas; nenhum resultado parcial foi usado")
            try:
                state = json.loads(progress.read_text())
                done, total = state.get("completed"), state.get("total")
                # Stage-relative counts, not an invented whole-episode percentage.
                progress_cb(5, f"CPU · {state['step']} · {done or 0}/{total or '?'} etapas · {int(time.monotonic()-started)}s")
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(1)
        if process.returncode:
            raise RuntimeError("Pyannote CPU falhou. Verifique instalação, memória e acesso do HF_TOKEN ao modelo community-1; não houve fallback visual.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
    document = DiarizationDocument.model_validate_json(output.read_text())
    if document.effective_device != "cpu" or not document.turns:
        raise ValueError("Diarização sem vozes válidas em CPU")
    artifact = domain.create_stage_artifact(StageArtifact(project_id=source.project_id,stage="diarization",schema_version=1,path=str(output),input_hash=key,metadata={"source_asset_id":source.id,"source_sha256":source.sha256,"device":"cpu","speakers":speakers,"versions":versions}))
    progress_cb(100, "Vozes separadas e salvas; confirme qual é a do Dayon")
    return {"diarization_artifact_id":artifact.id,"cached":False}

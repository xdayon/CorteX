from __future__ import annotations

import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator



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
    # A venv's Python is normally a symlink. Resolving its target bypasses
    # pyvenv.cfg and runs the base interpreter without the venv's packages.
    return Path(os.environ.get("CORTEX_DIARIZATION_PYTHON", ".venv-diarization/bin/python")).expanduser().absolute()

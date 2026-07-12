from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SafeZone(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    top: float = Field(ge=0, le=1)
    right: float = Field(ge=0, le=1)
    bottom: float = Field(ge=0, le=1)
    left: float = Field(ge=0, le=1)


SAFE_ZONES_VERSION = 1

SAFE_ZONES: dict[str, SafeZone] = {
    "9:16": SafeZone(top=0.07, right=0.07, bottom=0.16, left=0.07),
    "1:1": SafeZone(top=0.08, right=0.08, bottom=0.14, left=0.08),
    "16:9": SafeZone(top=0.10, right=0.10, bottom=0.12, left=0.10),
}

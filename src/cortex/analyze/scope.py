"""Validated source-time coverage for selected-clip visual analysis."""
from pydantic import BaseModel, ConfigDict, Field, model_validator


class SourceRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(ge=0, allow_inf_nan=False)
    end: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("intervalo deve ter end > start")
        return self


def normalize_ranges(ranges: list[SourceRange], duration: float) -> list[SourceRange]:
    result: list[SourceRange] = []
    for item in sorted(ranges, key=lambda r: r.start):
        start, end = round(item.start, 6), round(min(item.end, duration), 6)
        if end <= start:
            raise ValueError("intervalo fora da duração da fonte")
        if result and start <= result[-1].end:
            result[-1].end = max(result[-1].end, end)
        else:
            result.append(SourceRange(start=start, end=end))
    return result


def intersect_chunks(chunks: list[tuple[float, float]], ranges: list[SourceRange]):
    if not ranges:
        return chunks
    return [(max(a, r.start), min(b, r.end)) for a, b in chunks for r in ranges
            if min(b, r.end) > max(a, r.start)]

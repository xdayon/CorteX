"""Compact summary of past clip results, fed to the selection prompt."""

from __future__ import annotations

from math import ceil

from cortex.domain.models import ClipPublication
from cortex.domain.store import DomainStore

MIN_RECORDS = 3
MIN_VIEWS_FOR_ENGAGEMENT = 100
MAX_BEST = 5
MAX_WORST = 3
_HOOK_CHARS = 60
_HEADLINE_CHARS = 60
_RATING_LABEL = {1: "ótimo", 0: "ok", -1: "ruim"}


def _engagement(item: ClipPublication) -> float | None:
    if item.views is None or item.views < MIN_VIEWS_FOR_ENGAGEMENT:
        return None
    return ((item.shares or 0) + (item.saves or 0)) / item.views


def _rank_key(item: ClipPublication) -> tuple[int, float, int, str]:
    rating_rank = 1 if item.rating is None else item.rating + 1
    engagement = _engagement(item)
    return (
        -rating_rank,
        -(engagement if engagement is not None else -1.0),
        -(item.views or 0),
        item.render_artifact_id,
    )


def _decimal(value: float) -> str:
    return f"{value:.1f}".replace(".", ",")


def _views_label(views: int) -> str:
    if views >= 1_000_000:
        return f"{_decimal(views / 1_000_000)} mi views"
    if views >= 1_000:
        return f"{_decimal(views / 1_000)} mil views"
    return f"{views} views"


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _line(item: ClipPublication) -> str:
    label = _clip(item.headline or item.title, _HEADLINE_CHARS)
    parts = [f'"{label}"']
    if item.hook_text.strip():
        parts.append(f'gancho: "{_clip(item.hook_text, _HOOK_CHARS)}"')
    if item.duration_seconds > 0:
        parts.append(f"{round(item.duration_seconds)} s")
    if item.views is not None:
        metric = _views_label(item.views)
        engagement = _engagement(item)
        if engagement is not None:
            metric += f" · {_decimal(engagement * 100)}% compart.+salv."
        parts.append(metric)
    if item.rating is not None:
        parts.append(f"nota: {_RATING_LABEL[item.rating]}")
    return "- " + " — ".join(parts)


def build_learning_context(domain: DomainStore) -> str:
    items = [
        item for item in domain.list_clip_publications(limit=500)
        if item.rating is not None or item.views is not None
    ]
    if len(items) < MIN_RECORDS:
        return ""
    ranked = sorted(items, key=_rank_key)
    best_count = min(MAX_BEST, ceil(len(ranked) / 2))
    worst_count = min(MAX_WORST, len(ranked) - best_count)
    best = ranked[:best_count]
    worst = list(reversed(ranked[len(ranked) - worst_count:])) if worst_count else []
    lines = ["## Aprendizado do perfil", "", "Funcionaram melhor:"]
    lines += [_line(item) for item in best]
    if worst:
        lines += ["", "Funcionaram pior:"]
        lines += [_line(item) for item in worst]
    lines += [
        "",
        "Calibre ganchos, temas e títulos por esses padrões, sem copiar frases.",
    ]
    return "\n".join(lines)

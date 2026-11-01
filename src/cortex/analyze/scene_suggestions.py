"""Scene suggestions for one clip: cutaways (reaction, wide shot) and the interviewer's question.

Deterministic and cheap: the only media decoded is a few small frames of the chosen
borrowed footage, through the optional ``verify`` (``footage_check``), because the
keyframe-only camera map misses cuts without a keyframe. The episode's camera map says
which camera is on air at every second, the voiceprint turns say who speaks, and the transcript
says where phrases end. A reaction is footage of the interviewers' camera from another
moment while only the subject spoke (they listen in silence); a wide shot is the studio
camera while the subject spoke (he gestures as if speaking) or, when the episode has no
usable studio footage, the camera on air at that very moment without the zoom (a two-shot
or wider, so the set shows). Inserts only replace the picture (``RenderInsert``): the
clip's audio and captions go on.

The suggestions of a clip are its triangulation: the first one establishes the studio
(wide), then reactions and wides alternate two to one, with at least one reaction.
"""
from __future__ import annotations

import bisect
import hashlib
import math
import re
from collections.abc import Callable, Sequence

from pydantic import BaseModel, ConfigDict

from cortex.analyze.camera_map import CameraMapDocument
from cortex.edit.schemas import EditPlanDocument
from cortex.transcribe.schemas import TranscriptWord

SCENE_SUGGESTIONS_VERSION = "2"
HOOK_SECONDS = 3.0          # never cover the opening hook
JOIN_MARGIN = 1.0           # keep inserts inside one segment, away from its cuts
MIN_INSERT, DEFAULT_INSERT, MAX_INSERT = 1.2, 2.0, 2.5
SPACING = 8.0               # between insert starts on the output timeline
SECONDS_PER_INSERT = 15.0   # at most one suggestion per this much clip
MIN_SUGGESTIONS = 2         # every clip tries for a wide and a reaction
# The on-air camera unzoomed ranks below real studio footage; a close-up unzoomed shows
# less of the set than a two-shot, so it is the last resort.
LIVE_WIDE_SCORE, LIVE_CLOSE_UP_SCORE = 0.35, 0.2
SHARED_REACTION = 0.7       # a reaction from a two-face group: one face is better
SILENT_MARGIN = 0.4         # the others stay silent this long around the footage
SUBJECT_PAUSE = 0.3         # a pause this short inside the subject's speech still counts
SHOT_MARGIN = 0.3           # footage stays this far from its own camera cuts
CLIP_MARGIN = 0.5           # footage never repeats what the clip already shows
PROXIMITY_SECONDS = 600.0   # nearby moments look alike (light, posture, topic)
QUESTION_MAX, QUESTION_MIN, QUESTION_GAP = 6.0, 1.0, 4.0
ALTERNATIVES = 3
VERIFIED_PER_KIND = 3       # borrowed candidates decoded per insert when a verifier is given
_SENTENCE_END = re.compile(r"[.!?…:;][\"')\]]*$")
_CLAUSE_END = re.compile(r",[\"')\]]*$")
_FOOTAGE_ROLE = {"reaction": "interviewers", "wide": "wide"}


class SceneCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str
    source_start: float
    group: int
    score: float


class SceneSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    kind: str
    anchor: float
    duration: float
    output_start: float
    source_start: float
    group: int
    score: float
    reason: str
    alternatives: list[SceneCandidate]


class QuestionSuggestion(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float
    end: float
    text: str


class SceneSuggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    algorithm_version: str = SCENE_SUGGESTIONS_VERSION
    suggestions: list[SceneSuggestion]
    question: QuestionSuggestion | None = None


Interval = tuple[float, float]


def merge(intervals: Sequence[Interval]) -> list[Interval]:
    merged: list[Interval] = []
    for start, end in sorted(intervals):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def subtract(intervals: Sequence[Interval], cuts: Sequence[Interval]) -> list[Interval]:
    """``intervals`` minus the merged ``cuts``."""
    out: list[Interval] = []
    cuts = merge(cuts)
    for start, end in intervals:
        cursor = start
        for cut_start, cut_end in cuts:
            if cut_end <= cursor or cut_start >= end:
                continue
            if cut_start > cursor:
                out.append((cursor, cut_start))
            cursor = max(cursor, cut_end)
        if cursor < end:
            out.append((cursor, end))
    return out


def intersect(a: Sequence[Interval], b: Sequence[Interval]) -> list[Interval]:
    out: list[Interval] = []
    i = j = 0
    while i < len(a) and j < len(b):
        start, end = max(a[i][0], b[j][0]), min(a[i][1], b[j][1])
        if start < end:
            out.append((start, end))
        if a[i][1] < b[j][1]:
            i += 1
        else:
            j += 1
    return out


def subject_only(turns: Sequence, subject: str) -> list[Interval]:
    """Where the subject speaks and nobody else does, with ``SILENT_MARGIN`` around the others."""
    others = merge([(t.start - SILENT_MARGIN, t.end + SILENT_MARGIN) for t in turns if t.speaker != subject])
    spoken = merge([(t.start - SUBJECT_PAUSE, t.end + SUBJECT_PAUSE) for t in turns if t.speaker == subject])
    return subtract(spoken, others)


def output_starts(plan: EditPlanDocument) -> list[float]:
    """Output-timeline start of every segment, with the render's crossfade accounting."""
    starts: list[float] = []
    elapsed = 0.0
    for index, segment in enumerate(plan.segments):
        duration = segment.video_end - segment.video_start
        if index == 0:
            starts.append(0.0)
            elapsed = duration
            continue
        previous = plan.segments[index - 1]
        fade = previous.transition.duration if previous.transition is not None else 0.0
        fade = max(0.0, min(fade, (previous.video_end - previous.video_start) / 2, duration / 2))
        starts.append(elapsed - fade)
        elapsed += duration - fade
    return starts


def _on_air(camera_map: CameraMapDocument, starts: list[float], time: float) -> int | None:
    index = bisect.bisect_right(starts, time) - 1
    if index < 0:
        return None
    shot = camera_map.shots[index]
    return shot.group if shot.start <= time < shot.end else None


def _covers(intervals: Sequence[Interval], start: float, end: float) -> bool:
    index = bisect.bisect_right([a for a, _b in intervals], start) - 1
    return index >= 0 and intervals[index][1] >= end


def _footage_windows(
    camera_map: CameraMapDocument, roles: dict[int, str], allowed: list[Interval],
    plan: EditPlanDocument,
) -> dict[str, list[tuple[float, float, int, float]]]:
    """Per kind: (start, end, group, shot length) stretches usable as footage."""
    shown = [(s.video_start - CLIP_MARGIN, s.video_end + CLIP_MARGIN) for s in plan.segments]
    windows: dict[str, list[tuple[float, float, int, float]]] = {kind: [] for kind in _FOOTAGE_ROLE}
    for kind, role in _FOOTAGE_ROLE.items():
        for shot in camera_map.shots:
            if roles.get(shot.group) != role or shot.end - shot.start < MIN_INSERT + 2 * SHOT_MARGIN:
                continue
            inner = [(shot.start + SHOT_MARGIN, shot.end - SHOT_MARGIN)]
            for start, end in subtract(intersect(inner, allowed), shown):
                if end - start >= MIN_INSERT:
                    windows[kind].append((start, end, shot.group, shot.end - shot.start))
    return windows


def _candidates(
    windows: list[tuple[float, float, int, float]], kind: str, anchor: float, duration: float,
    faces: dict[int, int], used: list[Interval],
) -> list[SceneCandidate]:
    found: list[SceneCandidate] = []
    for start, end, group, shot_length in windows:
        if end - start < duration:
            continue
        # The start of the stretch (the shot's own keyframe is close, so checking the
        # footage decodes little), else right beside footage already used: a long
        # interviewers shot can feed several inserts of one clip.
        spots = [start]
        spots += [spot for a, b in used for spot in (b, a - duration) if start <= spot <= end - duration]
        free = [s for s in spots if not any(s < b and s + duration > a for a, b in used)]
        if not free:
            continue
        source_start = min(free, key=lambda s: abs(s - anchor))
        proximity = math.exp(-abs(source_start - anchor) / PROXIMITY_SECONDS)
        stability = min(1.0, shot_length / 6.0)
        visible = 1.0 if faces.get(group, 0) >= 1 else 0.5
        shared = SHARED_REACTION if kind == "reaction" and faces.get(group, 0) >= 2 else 1.0
        found.append(SceneCandidate(
            kind=kind, source_start=round(source_start, 3), group=group,
            score=round((0.6 * proximity + 0.3 * stability + 0.1 * visible) * shared, 4),
        ))
    found.sort(key=lambda c: (-c.score, c.source_start))
    return found


def _distance(seconds: float) -> str:
    amount = abs(seconds)
    text = f"{round(amount / 60)} min" if amount >= 90 else f"{max(1, round(amount))} s"
    return f"{text} {'depois' if seconds > 0 else 'antes'}"


def _reason(kind: str, anchor: float, source_start: float) -> str:
    if kind == "wide" and abs(source_start - anchor) < 1e-3:
        return "Plano aberto da câmera no ar, sem zoom"
    what = "Entrevistadores ouvindo calados" if kind == "reaction" else "Estúdio em plano aberto"
    return f"{what}, {_distance(source_start - anchor)} no episódio"


def _live_wide(
    camera_map: CameraMapDocument, shot_starts: list[float], faces: dict[int, int],
    anchor: float, duration: float,
) -> SceneCandidate | None:
    """The camera on air at the anchor, unzoomed: in sync with the subject's voice, so it
    needs no silent moment. The whole frame over the blur shows the set around him."""
    group = _on_air(camera_map, shot_starts, anchor)
    if group is None or _on_air(camera_map, shot_starts, anchor + duration) != group:
        return None
    score = LIVE_WIDE_SCORE if faces.get(group, 0) >= 2 else LIVE_CLOSE_UP_SCORE
    return SceneCandidate(kind="wide", source_start=round(anchor, 3), group=group, score=score)


def _kind_for(position: int, count: int) -> str:
    """Wide first (it situates the viewer), then reaction, reaction, wide…; a clip with a
    single insert shows the interviewer."""
    if count == 1:
        return "reaction"
    return "wide" if position % 3 == 0 else "reaction"


def _slots(
    plan: EditPlanDocument, words: list[TranscriptWord], starts: list[float], earliest: float,
) -> list[tuple[float, float, float, float]]:
    """(anchor, duration, output_start, weight) right after phrase ends, inside one body segment."""
    ends = [w.end for w in words]
    slots: list[tuple[float, float, float, float]] = []
    for segment, out_start in zip(plan.segments, starts):
        if segment.teaser or segment.question:
            continue
        limit = segment.video_end - JOIN_MARGIN
        low = bisect.bisect_left(ends, segment.video_start + JOIN_MARGIN)
        high = bisect.bisect_right(ends, limit - MIN_INSERT)
        for word in words[low:high]:
            text = word.word.strip()
            weight = 1.0 if _SENTENCE_END.search(text) else 0.85 if _CLAUSE_END.search(text) else 0.0
            output_start = out_start + word.end - segment.video_start
            if not weight or output_start < earliest:
                continue
            # Cut back to the subject at the next phrase end when one is close.
            closing = None
            for candidate in words[bisect.bisect_left(ends, word.end + MIN_INSERT):]:
                if candidate.end > min(word.end + MAX_INSERT, limit):
                    break
                if _SENTENCE_END.search(candidate.word.strip()) or _CLAUSE_END.search(candidate.word.strip()):
                    closing = candidate.end
                    break
            duration = (closing - word.end) if closing else min(DEFAULT_INSERT, limit - word.end)
            if duration >= MIN_INSERT:
                slots.append((word.end, round(duration, 3), output_start, weight))
    return slots


def detect_question(
    plan: EditPlanDocument, words: list[TranscriptWord], turns: Sequence, subject: str,
) -> QuestionSuggestion | None:
    """The end (<= ``QUESTION_MAX`` s) of the others' turn that ends right before the answer."""
    body = [s for s in plan.segments if not s.teaser and not s.question]
    if not body:
        return None
    answer = min(body[0].start, body[0].video_start, body[0].audio_start)
    turn = max((
        t for t in turns
        if t.speaker != subject and answer - QUESTION_GAP <= t.end <= answer + 0.5
        and t.end - t.start >= QUESTION_MIN
    ), key=lambda t: t.end, default=None)
    if turn is None:
        return None
    limit = min(turn.end, answer)
    inside = [w for w in words if w.start >= turn.start - 0.1 and w.end <= limit + 0.05]
    if not inside:
        return None
    end = inside[-1].end
    window = [w for w in inside if end - QUESTION_MAX <= w.start <= end - QUESTION_MIN]
    if not window:
        return None
    previous = {id(b): a for a, b in zip(inside, inside[1:])}

    def opens_sentence(word: TranscriptWord) -> bool:
        before = previous.get(id(word))
        return before is None or bool(_SENTENCE_END.search(before.word.strip())) or word.start - before.end >= 0.4

    first = next((w for w in window if opens_sentence(w)), window[0])
    chosen = [w for w in inside if w.start >= first.start]
    return QuestionSuggestion(
        start=round(first.start, 3), end=round(end, 3), text=" ".join(w.word.strip() for w in chosen),
    )


Verify = Callable[[Sequence[tuple[str, float, float]]], list[bool]]


def _checked(found: dict[str, list[SceneCandidate]], duration: float, anchor: float, verify: Verify) -> dict[str, list[SceneCandidate]]:
    """Keeps the live wide and the borrowed candidates the verifier confirms: the best
    few per kind, checked in one batch; the rest are dropped unchecked."""
    def live(candidate: SceneCandidate) -> bool:
        return abs(candidate.source_start - anchor) < 1e-3

    batch = [(kind, c) for kind in found for c in [c for c in found[kind] if not live(c)][:VERIFIED_PER_KIND]]
    passed = verify([(kind, c.source_start, duration) for kind, c in batch])
    confirmed = {id(c) for (_kind, c), ok in zip(batch, passed) if ok}
    return {kind: [c for c in found[kind] if live(c) or id(c) in confirmed] for kind in found}


def suggest_scenes(
    plan: EditPlanDocument, camera_map: CameraMapDocument, roles: dict[int, str],
    turns: Sequence, words: list[TranscriptWord], subject: str, verify: Verify | None = None,
) -> SceneSuggestions:
    """``verify`` decodes borrowed footage to confirm the camera (``FootageChecker``);
    without it the camera map alone is trusted."""
    words = sorted(words, key=lambda w: w.start)
    turns = sorted(turns, key=lambda t: t.start)
    question = detect_question(plan, words, turns, subject)
    starts = output_starts(plan)
    body_start = next((out for s, out in zip(plan.segments, starts) if not s.teaser and not s.question), 0.0)
    earliest = max(HOOK_SECONDS, body_start + JOIN_MARGIN if body_start > 0 else HOOK_SECONDS)
    allowed = subject_only(turns, subject)
    others = merge([(t.start, t.end) for t in turns if t.speaker != subject])
    shot_starts = [shot.start for shot in camera_map.shots]
    faces = {group.id: group.faces for group in camera_map.groups}
    windows = _footage_windows(camera_map, roles, allowed, plan)

    def options(anchor: float, duration: float, used: list[Interval]) -> dict[str, list[SceneCandidate]]:
        found = {kind: _candidates(windows[kind], kind, anchor, duration, faces, used) for kind in _FOOTAGE_ROLE}
        live = _live_wide(camera_map, shot_starts, faces, anchor, duration)
        if live is not None:
            found["wide"] = sorted([*found["wide"], live], key=lambda c: (-c.score, c.source_start))
        return found

    scored = []
    for anchor, duration, output_start, weight in _slots(plan, words, starts, earliest):
        # The clip itself must show the subject speaking, with no one else's voice.
        if any(a < anchor + duration + 0.2 and b > anchor - 0.2 for a, b in others):
            continue
        if any(roles.get(_on_air(camera_map, shot_starts, t)) != "subject" for t in (anchor, anchor + duration)):
            continue
        best = max((found[0] for found in options(anchor, duration, []).values() if found),
                   key=lambda c: c.score, default=None)
        if best is not None:
            scored.append((best.score * weight, anchor, duration, output_start))

    limit = max(MIN_SUGGESTIONS, int(plan.timeline_duration_seconds // SECONDS_PER_INSERT))
    picked: list[tuple[float, float, float]] = []
    for _score, anchor, duration, output_start in sorted(scored, key=lambda item: (-item[0], item[3])):
        if len(picked) >= limit:
            break
        if all(abs(output_start - other) >= SPACING for _a, _d, other in picked):
            picked.append((anchor, duration, output_start))

    suggestions: list[SceneSuggestion] = []
    used: list[Interval] = []
    ordered = sorted(picked, key=lambda item: item[2])
    for position, (anchor, duration, output_start) in enumerate(ordered):
        found = options(anchor, duration, used)
        if verify is not None:
            found = _checked(found, duration, anchor, verify)
        kind = _kind_for(position, len(ordered))
        # The last slots go to a kind the clip still lacks, the interviewer first.
        missing = [k for k in ("reaction", "wide") if found[k] and all(s.kind != k for s in suggestions)]
        if missing and len(ordered) - position <= len(missing) and kind not in missing:
            kind = missing[0]
        if not found[kind]:
            kind = "wide" if kind == "reaction" else "reaction"
        if not found[kind]:
            continue
        chosen, *rest = found[kind]
        other = "wide" if kind == "reaction" else "reaction"
        alternatives = [*rest[:ALTERNATIVES - 1], *found[other][:1]]
        if abs(chosen.source_start - anchor) >= 1e-3:  # the live wide is the clip's own moment
            used.append((chosen.source_start - CLIP_MARGIN, chosen.source_start + duration + CLIP_MARGIN))
        identity = f"{SCENE_SUGGESTIONS_VERSION}:{kind}:{anchor:.2f}:{chosen.source_start:.2f}"
        suggestions.append(SceneSuggestion(
            id="sc-" + hashlib.sha1(identity.encode()).hexdigest()[:12], kind=kind,
            anchor=round(anchor, 3), duration=duration, output_start=round(output_start, 3),
            source_start=chosen.source_start, group=chosen.group, score=chosen.score,
            reason=_reason(kind, anchor, chosen.source_start), alternatives=alternatives,
        ))
    return SceneSuggestions(suggestions=suggestions, question=question)


"""Persistent personal episode catalogue, grouped by canonical YouTube identity."""
from __future__ import annotations

import fcntl
from functools import wraps
import json
import re
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from pydantic import BaseModel, ConfigDict, Field

from cortex.config import CortexConfig
from cortex.domain.store import DomainStore
from cortex.schemas import JobCreate, JobType


class Episode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    url: str | None = None
    title: str
    project_ids: list[str]
    participant_count: int | None = None
    primary_subject: str = "Dayon"
    subject_reference: dict = Field(default_factory=dict)


def youtube_id(url: str) -> str:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    if parsed.scheme not in {"https", "http"}:
        raise ValueError("Link do YouTube inválido")
    if host in {"youtu.be", "www.youtu.be"}:
        value = parsed.path.strip("/").split("/")[0]
    elif host in {"youtube.com", "www.youtube.com", "m.youtube.com"}:
        pieces = parsed.path.strip("/").split("/")
        value = parse_qs(parsed.query).get("v", [""])[0] if parsed.path == "/watch" else (pieces[1] if len(pieces)>1 and pieces[0] in {"live", "shorts", "embed"} else "")
    else:
        raise ValueError("Use um link do YouTube")
    if not re.fullmatch(r"[A-Za-z0-9_-]{11}", value):
        raise ValueError("ID de vídeo inválido")
    return value


def serialized(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with Path(str(self.config.paths.database) + ".library.lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            return method(self, *args, **kwargs)
    return guarded


class EpisodeLibrary:
    def __init__(self, config: CortexConfig, domain: DomainStore):
        self.config, self.domain = config, domain
        with self.connect() as connection:
            connection.execute("CREATE TABLE IF NOT EXISTS episode_library (id TEXT PRIMARY KEY, data TEXT NOT NULL)")

    def connect(self):
        return sqlite3.connect(self.config.paths.database, timeout=30)

    def save(self, episode: Episode):
        with self.connect() as connection:
            connection.execute("INSERT INTO episode_library VALUES (?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data", (episode.id, episode.model_dump_json()))
        return episode

    def get(self, episode_id: str):
        with self.connect() as connection:
            row = connection.execute("SELECT data FROM episode_library WHERE id=?", (episode_id,)).fetchone()
        if row is None:
            raise ValueError("Episódio não encontrado")
        return Episode.model_validate_json(row[0])

    @serialized
    def register(self, url: str, participant_count: int | None = None):
        key = youtube_id(url)
        try:
            episode = self.get(key)
            if participant_count is not None:
                episode.participant_count = participant_count
            return self.save(episode)
        except ValueError:
            pass
        projects = []
        for project in self.domain.list_projects(limit=10000):
            candidates = [project.name] + [a.source_url or "" for a in self.domain.list_source_assets(project.id)]
            for candidate in candidates:
                try:
                    if youtube_id(candidate) == key:
                        projects.append(project.id)
                        break
                except ValueError:
                    continue
        if not projects:
            projects = [self.domain.create_project(f"YouTube · {key}").id]
        return self.save(Episode(id=key, url=f"https://www.youtube.com/watch?v={key}", title=f"Episódio · {key}", project_ids=projects, participant_count=participant_count))

    def reconcile(self):
        """Expose existing saved media without moving or deleting any artifact."""
        for project in self.domain.list_projects(limit=10000):
            sources = self.domain.list_source_assets(project.id)
            for source in sources:
                if source.source_url:
                    try:
                        episode = self.register(source.source_url)
                        if project.id not in episode.project_ids:
                            episode.project_ids.append(project.id)
                        if source.original_filename:
                            episode.title = source.original_filename
                        self.save(episode)
                    except ValueError:
                        pass
            if sources and not any(source.source_url for source in sources):
                key = f"local-{project.id}"
                try:
                    self.get(key)
                except ValueError:
                    self.save(Episode(id=key, title=project.name, project_ids=[project.id]))

    def entries(self):
        self.reconcile()
        with self.connect() as connection:
            rows = connection.execute("SELECT data FROM episode_library ORDER BY rowid").fetchall()
        return [Episode.model_validate_json(r[0]) for r in rows]

    def detail(self, episode: Episode):
        sources, runs, renders, suggestions = [], [], [], []
        for project_id in episode.project_ids:
            sources.extend(self.domain.list_source_assets(project_id))
            runs.extend(self.domain.list_workflow_runs(project_id))
            for artifact in self.domain.list_stage_artifacts(project_id):
                if artifact.stage not in {"render", "suggestion"} or not Path(artifact.path).is_file():
                    continue
                try:
                    doc = json.loads(Path(artifact.path).read_text())
                    if artifact.stage == "render":
                        if not Path(doc["output_path"]).is_file():
                            continue
                        renders.append({"id":artifact.id,"project_id":project_id,"created_at":artifact.created_at,"duration":doc.get("timeline_duration_seconds"),"title":(doc.get("effective_settings") or {}).get("headline",{}).get("text") or "Corte exportado","publish_ready":(doc.get("publication") or {}).get("publish_ready",False)})
                    else:
                        suggestions.append({"id":artifact.id,"project_id":project_id,"clips":doc.get("selection",{}).get("clips",[])})
                except (ValueError, KeyError, OSError):
                    continue
        available = [s for s in sources if (self.config.paths.data_dir/s.stored_path).is_file()]
        available.sort(key=lambda s: s.created_at)
        with self.connect() as connection:
            placeholders = ",".join("?" for _ in episode.project_ids)
            jobs = [json.loads(r[0]) for r in connection.execute(f"SELECT data FROM jobs WHERE json_extract(data,'$.project_id') IN ({placeholders}) ORDER BY created_at DESC",episode.project_ids)]
        title = next((s.original_filename for s in available if s.original_filename),episode.title)
        return {**episode.model_dump(),"title":title,"source":available[0].model_dump(mode="json") if available else None,
                "source_bytes":sum({s.stored_path:s.size_bytes for s in available}.values()),"runs":[r.model_dump(mode="json") for r in sorted(runs,key=lambda r:r.created_at,reverse=True)],"renders":renders,"suggestions":suggestions,
                "diarizations":[{"id": a.id, "project_id":p} for p in episode.project_ids for a in self.domain.list_stage_artifacts(p,stage="diarization") if Path(a.path).is_file()],
                "jobs":[{k:j.get(k) for k in ("id","type","status","progress","message","error")} for j in jobs[:30]]}

    @serialized
    def download(self, episode: Episode, jobs):
        detail = self.detail(episode)
        if detail["source"]:
            return {"source":detail["source"],"job":None,"cached":True}
        if not episode.url:
            raise ValueError("Arquivo local ausente; importe novamente")
        active = next((j for j in detail["jobs"] if j["type"]=="ingest_youtube" and j["status"] in {"queued","running"}),None)
        if active:
            return {"source":None,"job":jobs.get(active["id"]),"cached":False}
        job = jobs.create(JobCreate(type=JobType.INGEST_YOUTUBE,project_id=episode.project_ids[0],payload={"url":episode.url}))
        return {"source":None,"job":job,"cached":False}

"""Opt-in local voice pipeline; supply a short, consented speech recording."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil

import pytest
from asgi_client import ASGITestClient
from conftest import FakeTranscriptionEngine
from cortex.api import create_app
from cortex.diarize.service import DiarizationDocument
from cortex.domain.models import SourceAsset, SourceKind
from cortex.domain.store import DomainStore
from cortex.ingest.ffprobe import probe_media
from cortex.jobs import JobStore
from cortex.worker import process_next
from test_render import _config


@pytest.mark.skipif(os.environ.get('CORTEX_RUN_DIARIZATION_E2E') != '1',
                    reason='requires CPU runtime, model access and CORTEX_DIARIZATION_TEST_AUDIO')
def test_episode_button_worker_persists_real_voices_and_reuses_cache(tmp_path):
    sample = Path(os.environ['CORTEX_DIARIZATION_TEST_AUDIO']).absolute()
    config = _config(tmp_path)
    config.ensure_runtime_dirs()
    jobs = JobStore(config.paths.database)
    domain = DomainStore(config.paths.database)
    project = domain.create_project('Voice pipeline verification — temporary database')
    audio = tmp_path / 'sample.wav'
    shutil.copyfile(sample, audio)
    domain.create_source_asset(SourceAsset(
        project_id=project.id, kind=SourceKind.UPLOAD, original_filename=audio.name,
        stored_path=audio.name, sha256=hashlib.sha256(audio.read_bytes()).hexdigest(),
        size_bytes=audio.stat().st_size, probe=probe_media(config.render.ffprobe, audio),
    ))
    client = ASGITestClient(create_app(config))
    entries = client.get('/api/v1/episodes').json()
    episode = next(e for e in entries if project.id in e['project_ids'])
    assert client.get('/api/v1/diarization-status').json()['ready']
    endpoint = f"/api/v1/episodes/{episode['id']}/diarization"
    first = client.post(endpoint)
    assert first.status_code == 200, first.text
    assert process_next(config, jobs, domain, FakeTranscriptionEngine())
    done = jobs.get(first.json()['id'])
    assert done.status.value == 'succeeded', done.error
    artifact_id = done.result['diarization_artifact_id']
    artifact = domain.get_stage_artifact(artifact_id)
    document = DiarizationDocument.model_validate_json(Path(artifact.path).read_text())
    assert document.effective_device == 'cpu' and document.turns
    samples = client.get(f'{endpoint}/{artifact_id}')
    assert samples.status_code == 200 and samples.json()['turns']
    speaker = document.turns[0].speaker
    # Only this isolated fixture is assigned a label; no production identity changes.
    selected = client.request('PUT', f"/api/v1/episodes/{episode['id']}/voice", json={
        'artifact_id': artifact_id, 'speaker': speaker,
    })
    assert selected.status_code == 200
    assert selected.json()['subject_reference']['speaker'] == speaker
    repeated = client.post(endpoint)
    assert repeated.status_code == 200
    assert process_next(config, jobs, domain, FakeTranscriptionEngine())
    cached = jobs.get(repeated.json()['id'])
    assert cached.status.value == 'succeeded', cached.error
    assert cached.result['cached'] and cached.result['diarization_artifact_id'] == artifact_id
    (tmp_path / 'voice-e2e-proof.json').write_text(json.dumps({
        'artifact': str(artifact.path), 'speakers': len({t.speaker for t in document.turns}),
        'turns': len(document.turns), 'elapsed_seconds': document.elapsed_seconds,
        'cache_reused': True, 'voice_choice_persisted': True,
    }, indent=2))

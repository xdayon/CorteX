from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cortex.domain.models import RenderPreset
from cortex.domain.store import (
    DomainStore,
    RenderPresetNameConflictError,
    RenderPresetNotFoundError,
)


class RenderPresetStoreTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory()
        self.store = DomainStore(Path(self.tempdir.name) / "domain.sqlite3")
        self.first_project = self.store.create_project("First")
        self.second_project = self.store.create_project("Second")

    def tearDown(self):
        self.tempdir.cleanup()

    def test_create_list_get_and_update_are_project_scoped(self):
        first = self.store.create_render_preset(RenderPreset(
            project_id=self.first_project.id,
            name="Vertical social",
            settings={"aspect_ratio": "9:16", "burn_subtitles": True},
        ))
        second = self.store.create_render_preset(RenderPreset(
            project_id=self.second_project.id,
            name="Vertical social",
            settings={"aspect_ratio": "1:1"},
        ))

        self.assertEqual(self.store.list_render_presets(self.first_project.id), [first])
        self.assertEqual(self.store.get_render_preset(self.first_project.id, first.id), first)
        with self.assertRaises(RenderPresetNotFoundError):
            self.store.get_render_preset(self.second_project.id, first.id)

        updated = self.store.update_render_preset(
            self.first_project.id,
            first.id,
            name="Square social",
            settings={"aspect_ratio": "1:1"},
        )
        self.assertEqual(updated.name, "Square social")
        self.assertEqual(updated.settings, {"aspect_ratio": "1:1"})
        self.assertEqual(updated.created_at, first.created_at)
        self.assertGreaterEqual(updated.updated_at, first.updated_at)
        self.assertEqual(self.store.get_render_preset(self.first_project.id, first.id), updated)
        self.assertEqual(self.store.get_render_preset(self.second_project.id, second.id), second)

    def test_name_must_be_unique_within_a_project(self):
        self.store.create_render_preset(RenderPreset(
            project_id=self.first_project.id, name="YouTube", settings={}
        ))
        with self.assertRaises(RenderPresetNameConflictError):
            self.store.create_render_preset(RenderPreset(
                project_id=self.first_project.id, name="YouTube", settings={}
            ))


if __name__ == "__main__":
    unittest.main()

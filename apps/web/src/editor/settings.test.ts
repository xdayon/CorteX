import { expect, test } from "vitest";
import { restoreEditorDraft } from "../editorDraft";
import { parseHotwords } from "./hotwords";
import { defaultRenderSettings, normalizeSettings } from "./settings";

test("legacy framing modes become the blurred frame and are flagged", () => {
  for (const mode of ["speaker_auto", "speaker_face", "face_static_crop"]) {
    const result = normalizeSettings({ ...defaultRenderSettings, framing: { ...defaultRenderSettings.framing, mode, primary_side: "left" } });
    expect(result.legacyFraming).toBe(true);
    expect(result.settings.framing).toMatchObject({ mode: "blurred_background", primary_side: null });
  }
  expect(normalizeSettings(defaultRenderSettings).legacyFraming).toBe(false);
});

test("old shapes are merged with the new defaults instead of crashing", () => {
  const old = structuredClone(defaultRenderSettings) as Record<string, any>;
  delete old.transitions; delete old.framing.zoom; delete old.captions.style; delete old.headline.persistent;
  old.captions.font_family = "Lato"; old.captions.font_size = 70;
  const { settings } = normalizeSettings(old);
  expect(settings.transitions).toEqual(defaultRenderSettings.transitions);
  expect(settings.framing.zoom).toBe(1);
  expect(settings.captions).toMatchObject({ style: "pop", font_family: "Montserrat", font_size: 70 });
  expect(settings.headline.persistent).toBe(false);
});

test("the server encoder overrides whatever was stored", () => {
  const stored = { ...defaultRenderSettings, encoder: "h264_nvenc" };
  expect(normalizeSettings(stored, defaultRenderSettings, "libx264").settings.encoder).toBe("libx264");
  const storage = { getItem: () => JSON.stringify({ schema_version: 1, settings: stored }) } as unknown as Storage;
  expect(restoreEditorDraft(storage, "p:r", ["k"], defaultRenderSettings, "libx264").settings.encoder).toBe("libx264");
});

test("a draft saved before the editor existed keeps the user's values and gains emphasis edits", () => {
  const old = structuredClone(defaultRenderSettings) as Record<string, any>;
  delete old.transitions; old.captions.font_size = 64;
  const storage = { getItem: (key: string) => key.startsWith("cortex-draft") ? JSON.stringify({ schema_version: 1, settings: old, emphasisEdits: { k: [1, 2], gone: [3] } }) : null } as unknown as Storage;
  const draft = restoreEditorDraft(storage, "p:r", ["k"], defaultRenderSettings, "libx264");
  expect(draft.settings.captions.font_size).toBe(64);
  expect(draft.settings.transitions.piece_junction).toBe("flash");
  expect(draft.emphasisEdits).toEqual({ k: [1, 2] });
});

test("hotwords are split on commas, trimmed, deduplicated and capped", () => {
  expect(parseHotwords(" Ana  Souza, ana souza ,, Quântica;Física\nTeste")).toEqual(["Ana Souza", "Quântica", "Física", "Teste"]);
  expect(parseHotwords(Array.from({ length: 30 }, (_, i) => `t${i}`).join(","))).toHaveLength(20);
  expect(parseHotwords("x".repeat(60))[0]).toHaveLength(40);
  expect(parseHotwords("  ")).toEqual([]);
});

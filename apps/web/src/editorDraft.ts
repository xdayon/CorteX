import type { CaptionCorrection, RenderSettings } from './api';
import { normalizeSettings } from './editor/settings';

export type VisualArtifacts = { scene: string; face: string; speaker: string; camera: string; quality: string; identity: string };
export type EditorDraft = {
  schema_version: 1;
  settings: RenderSettings;
  selectedKeys: string[];
  captionEdits: Record<string, CaptionCorrection[]>;
  headlines: Record<string, string>;
  emphasisEdits: Record<string, number[]>;
  focusedKey: string;
  rendered: Array<{title: string; url: string; subtitles: string}>;
  reuseReactions: boolean;
  visualArtifacts: VisualArtifacts | null;
};

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
function validSettings(s: RenderSettings): boolean {
  return s.schema_version === 1 && ['h264_nvenc', 'libx264'].includes(s.encoder)
    && [1080, 1920].includes(s.canvas.width) && [1080, 1920].includes(s.canvas.height) && s.canvas.fps === 30
    && ['vertical_crop', 'blurred_background'].includes(s.framing.mode)
    && s.framing.zoom >= 1 && s.framing.zoom <= 3.2 && s.framing.focus_x >= 0 && s.framing.focus_x <= 1
    && ['classic', 'word_reveal', 'pop', 'box'].includes(s.captions.style)
    && ['cut', 'flash', 'zoom'].includes(s.transitions.piece_junction)
    && [undefined, null, 'left', 'right'].includes(s.framing.primary_side)
    && Number.isFinite(s.framing.position_y ?? .5) && (s.framing.position_y ?? .5) >= 0 && (s.framing.position_y ?? .5) <= 1
    && Number.isInteger(s.captions.words_per_cue) && s.captions.words_per_cue >= 1 && s.captions.words_per_cue <= 12
    && s.captions.font_size >= 16 && s.captions.font_size <= 96
    && (s.captions.position_y ?? .78) >= .1 && (s.captions.position_y ?? .78) <= .9
    && ['none', 'fade', 'pop'].includes(s.captions.animation.style)
    && (!s.framing.scene_overrides || (Array.isArray(s.framing.scene_overrides) && s.framing.scene_overrides.every(item =>
      object(item) && Number.isInteger(item.scene_index) && Number(item.scene_index) >= 0 && ['left', 'right', 'full'].includes(String(item.target)))));
}

export type RestoredDraft = EditorDraft & {legacyFraming: boolean};

export function restoreEditorDraft(storage: Storage, runKey: string, clipKeys: string[], defaults: RenderSettings, encoder?: RenderSettings['encoder'] | null): RestoredDraft {
  const fallback = normalizeSettings(defaults, defaults, encoder).settings;
  const empty: RestoredDraft = {schema_version: 1, settings: fallback, legacyFraming: false, selectedKeys: clipKeys, captionEdits: {}, headlines: {}, emphasisEdits: {}, focusedKey: '', rendered: [], reuseReactions: false, visualArtifacts: null};
  try {
    const saved: unknown = JSON.parse(storage.getItem(`cortex-draft:${runKey}`) || 'null');
    if (saved !== null && (!object(saved) || (saved.schema_version !== undefined && saved.schema_version !== 1))) return empty;
    const draft = object(saved) ? saved : {};
    let legacy: unknown = {};
    try { legacy = JSON.parse(storage.getItem(`cortex-caption-edits:${runKey}`) || '{}'); } catch { /* A corrupt legacy cache does not replace a valid draft. */ }
    const corrections = object(draft.captionEdits) ? draft.captionEdits : object(legacy) ? legacy : {};
    const captionEdits: Record<string, CaptionCorrection[]> = {};
    for (const key of clipKeys) {
      const edits = corrections[key];
      if (Array.isArray(edits) && edits.every(edit => object(edit) && Number.isInteger(edit.word_index) && Number(edit.word_index) >= 0 && typeof edit.original === 'string' && typeof edit.text === 'string')) captionEdits[key] = edits;
    }
    const normalized = normalizeSettings(draft.settings, defaults, encoder);
    const validDraft = object(draft.settings) && validSettings(normalized.settings);
    const emphasisEdits: Record<string, number[]> = {};
    if (object(draft.emphasisEdits)) for (const key of clipKeys) {
      const list = draft.emphasisEdits[key];
      if (Array.isArray(list) && list.every(item => Number.isInteger(item) && item >= 0)) emphasisEdits[key] = list as number[];
    }
    const visual = draft.visualArtifacts;
    const selected = draft.selectedKeys;
    return {...empty,
      settings: validDraft ? normalized.settings : fallback,
      legacyFraming: validDraft && normalized.legacyFraming,
      emphasisEdits,
      selectedKeys: Array.isArray(selected) ? clipKeys.filter(key => selected.includes(key)) : clipKeys,
      captionEdits,
      headlines: object(draft.headlines) ? Object.fromEntries(Object.entries(draft.headlines).filter(([key, value]) => clipKeys.includes(key) && typeof value === 'string')) as Record<string, string> : {},
      focusedKey: typeof draft.focusedKey === 'string' && clipKeys.includes(draft.focusedKey) ? draft.focusedKey : '',
      rendered: Array.isArray(draft.rendered) ? draft.rendered.filter(item => object(item) && ['title', 'url', 'subtitles'].every(key => typeof item[key] === 'string')) : [],
      reuseReactions: draft.reuseReactions === true,
      visualArtifacts: object(visual) && ['scene', 'face', 'speaker', 'camera', 'quality', 'identity'].every(key => typeof visual[key] === 'string' && !!visual[key]) ? visual as VisualArtifacts : null,
    };
  } catch { return empty; }
}

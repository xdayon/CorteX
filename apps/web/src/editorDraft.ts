import type { CaptionCorrection, RenderSettings } from './api';

export type VisualArtifacts = { scene: string; face: string; speaker: string; camera: string; quality: string; identity: string };
export type EditorDraft = {
  schema_version: 1;
  settings: RenderSettings;
  selectedKeys: string[];
  captionEdits: Record<string, CaptionCorrection[]>;
  headlines: Record<string, string>;
  focusedKey: string;
  rendered: Array<{title: string; url: string; subtitles: string}>;
  reuseReactions: boolean;
  visualArtifacts: VisualArtifacts | null;
};

const object = (value: unknown): value is Record<string, unknown> => !!value && typeof value === 'object' && !Array.isArray(value);
const matches = (value: unknown, template: unknown): boolean => {
  if (object(template)) return object(value) && Object.entries(template).every(([key, item]) => matches(value[key], item));
  return typeof value === typeof template && (typeof value !== 'number' || Number.isFinite(value));
};
function validSettings(value: unknown, defaults: RenderSettings): value is RenderSettings {
  if (!matches(value, defaults)) return false;
  const s = value as RenderSettings;
  return s.schema_version === 1 && ['h264_nvenc', 'libx264'].includes(s.encoder)
    && [1080, 1920].includes(s.canvas.width) && [1080, 1920].includes(s.canvas.height) && s.canvas.fps === 30
    && ['vertical_crop', 'blurred_background', 'face_static_crop', 'speaker_auto'].includes(s.framing.mode)
    && Number.isInteger(s.captions.words_per_cue) && s.captions.words_per_cue >= 1 && s.captions.words_per_cue <= 12
    && s.captions.font_size >= 16 && s.captions.font_size <= 96
    && (s.captions.position_y ?? .78) >= .1 && (s.captions.position_y ?? .78) <= .9
    && ['none', 'fade', 'pop'].includes(s.captions.animation.style)
    && (!s.framing.scene_overrides || (Array.isArray(s.framing.scene_overrides) && s.framing.scene_overrides.every(item =>
      object(item) && Number.isInteger(item.scene_index) && Number(item.scene_index) >= 0 && ['left', 'right', 'full'].includes(String(item.target)))));
}

export function restoreEditorDraft(storage: Storage, runKey: string, clipKeys: string[], defaults: RenderSettings): EditorDraft {
  const empty: EditorDraft = {schema_version: 1, settings: defaults, selectedKeys: clipKeys, captionEdits: {}, headlines: {}, focusedKey: '', rendered: [], reuseReactions: false, visualArtifacts: null};
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
    const visual = draft.visualArtifacts;
    const selected = draft.selectedKeys;
    return {...empty,
      settings: validSettings(draft.settings, defaults) ? draft.settings : defaults,
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

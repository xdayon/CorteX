import type { RenderSettings } from "../api";

export const defaultRenderSettings: RenderSettings = {
  schema_version: 1,
  encoder: "libx264",
  canvas: { width: 1080, height: 1920, fps: 30 },
  framing: {
    schema_version: 1, mode: "blurred_background", position_y: 0.37, zoom: 1.45, focus_x: 0.5, focus_mode: "faces",
    punch_in: { enabled: true, scale: 1.12, anchor: "center", alternate_on_jump_cuts: true },
  },
  captions: {
    enabled: true, font_family: "Montserrat", font_size: 56, words_per_cue: 5,
    font_weight: 900, uppercase: true, position_y: 0.64,
    outline: true, shadow: true, karaoke: true,
    text_color: "#FFFFFF", karaoke_color: "#2CE4D7", outline_color: "#071012",
    shadow_color: "#000000B8", animation: { style: "fade", duration_seconds: 0.18 },
    style: "pop", emphasis_word_indices: [], emphasis_color: "#FFD400", box_text_color: "#071012",
  },
  headline: {
    enabled: true, text: "", font_family: "Montserrat", font_size: 48, duration_seconds: 4, persistent: false,
    burst_color: "#11B9AD", strip_color: "#FFFFFFF5", text_color: "#071012",
    animation: { entrance: "fade", exit: "fade", duration_seconds: 0.24 },
  },
  subtitles: { sidecar_srt: true },
  template: { quote_burst_opacity: 1, quote_strip_opacity: 0.96, quote_padding: 14 },
  transitions: { piece_junction: "flash", flash_opacity: 0.55, flash_seconds: 0.12 },
};

export const LEGACY_FRAMING_NOTICE =
  "Este episódio usava um enquadramento antigo (focado em quem fala). Ele foi trocado por “Quadro com fundo desfocado”.";

const LEGACY_FRAMING = new Set(["speaker_auto", "speaker_face", "face_static_crop"]);

const isObject = (value: unknown): value is Record<string, unknown> =>
  !!value && typeof value === "object" && !Array.isArray(value);

function merge(raw: unknown, template: unknown): unknown {
  if (isObject(template)) {
    const source = isObject(raw) ? raw : {};
    const out: Record<string, unknown> = { ...source };
    for (const key of Object.keys(template)) out[key] = merge(source[key], template[key]);
    return out;
  }
  if (Array.isArray(template)) return Array.isArray(raw) ? raw : template;
  const same = typeof raw === typeof template && (typeof raw !== "number" || Number.isFinite(raw));
  return same ? raw : template;
}

/** Fills fields missing from older drafts/revisions and replaces unsupported choices. */
export function normalizeSettings(raw: unknown, defaults: RenderSettings = defaultRenderSettings, encoder?: RenderSettings["encoder"] | null): {settings: RenderSettings; legacyFraming: boolean} {
  const settings = merge(raw, defaults) as RenderSettings;
  const rawFraming = isObject(raw) ? raw.framing : undefined;
  if (isObject(rawFraming) && typeof rawFraming.focus_mode !== "string") settings.framing = { ...settings.framing, focus_mode: "manual" };
  if (encoder) settings.encoder = encoder;
  let legacyFraming = false;
  if (LEGACY_FRAMING.has(settings.framing.mode)) {
    legacyFraming = true;
    settings.framing = { ...settings.framing, mode: "blurred_background", primary_side: null, scene_overrides: [] };
  }
  if (settings.captions.font_family !== "Montserrat") settings.captions = { ...settings.captions, font_family: "Montserrat" };
  if (settings.headline.font_family !== "Montserrat") settings.headline = { ...settings.headline, font_family: "Montserrat" };
  return { settings, legacyFraming };
}

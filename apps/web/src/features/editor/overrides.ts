import type { RenderSettings, SettingsPatch } from "../../api";

type Tree = Record<string, unknown>;
const isTree = (value: unknown): value is Tree => !!value && typeof value === "object" && !Array.isArray(value);
const same = (a: unknown, b: unknown) => JSON.stringify(a) === JSON.stringify(b);

/** Recursive merge; the override wins and nested sections keep the keys it does not mention (arrays replace). */
export function deepMerge<T>(base: T, override: unknown): T {
  if (!isTree(base) || !isTree(override)) return (override === undefined ? base : override) as T;
  const merged: Tree = { ...base };
  for (const [key, value] of Object.entries(override)) merged[key] = isTree(merged[key]) && isTree(value) ? deepMerge(merged[key], value) : value;
  return merged as T;
}

/** The settings a clip is previewed and exported with: shared settings under its own overrides. */
export const effectiveSettings = (shared: RenderSettings, override: SettingsPatch | undefined): RenderSettings =>
  override ? deepMerge(shared, override) : shared;

/** Only the leaves of `next` that differ from `base`, as a nested partial. */
export function diffSettings(base: unknown, next: unknown): Tree {
  const out: Tree = {};
  if (!isTree(base) || !isTree(next)) return out;
  for (const [key, value] of Object.entries(next)) {
    if (isTree(value) && isTree(base[key])) {
      const inner = diffSettings(base[key], value);
      if (Object.keys(inner).length) out[key] = inner;
    } else if (!same(base[key], value)) out[key] = value;
  }
  return out;
}

/** Removes from `override` every leaf that `change` mentions, dropping sections left empty. */
export function dropPaths(override: Tree, change: Tree): Tree {
  const out: Tree = {};
  for (const [key, value] of Object.entries(override)) {
    if (!(key in change)) { out[key] = value; continue; }
    if (isTree(value) && isTree(change[key])) {
      const inner = dropPaths(value, change[key] as Tree);
      if (Object.keys(inner).length) out[key] = inner;
    }
  }
  return out;
}

export type Overrides = Record<string, SettingsPatch>;

/**
 * Applies an edit made in the inspector. "all" edits the shared settings and clears the touched fields from every
 * clip override; "clip" stores only what differs from the shared settings in that clip's override.
 */
export function applySettingsEdit(
  shared: RenderSettings, overrides: Overrides, key: string, scope: "clip" | "all",
  change: (settings: RenderSettings) => RenderSettings,
): { shared: RenderSettings; overrides: Overrides } {
  if (scope === "all") {
    const next = change(shared);
    const touched = diffSettings(shared, next);
    if (!Object.keys(touched).length) return { shared, overrides };
    const cleaned: Overrides = {};
    for (const [clip, override] of Object.entries(overrides)) {
      const rest = dropPaths(override as Tree, touched);
      if (Object.keys(rest).length) cleaned[clip] = rest as SettingsPatch;
    }
    return { shared: next, overrides: cleaned };
  }
  const next = change(effectiveSettings(shared, overrides[key]));
  const patch = diffSettings(shared, next);
  const rest = { ...overrides };
  if (Object.keys(patch).length) rest[key] = patch as SettingsPatch; else delete rest[key];
  return { shared, overrides: rest };
}

/** Scenes (inserts and the opening question) are content, not style: they never count as a customized look and survive a style reset. */
export function withoutScenes(override: SettingsPatch | undefined): SettingsPatch {
  if (!override) return {};
  const { inserts: _inserts, transitions, ...rest } = override;
  if (!transitions) return rest;
  const { question: _question, ...style } = transitions;
  return Object.keys(style).length ? { ...rest, transitions: style } : rest;
}

export const isCustomized = (overrides: Overrides, key: string) => Object.keys(withoutScenes(overrides[key])).length > 0;

/** The override left after "voltar ao padrão": only the scenes stay. */
export function scenesOnly(override: SettingsPatch | undefined): SettingsPatch {
  const out: SettingsPatch = {};
  if (override?.inserts !== undefined) out.inserts = override.inserts;
  if (override?.transitions && "question" in override.transitions) out.transitions = { question: override.transitions.question };
  return out;
}

/** "Voltar ao padrão" for one clip: drops its style overrides and keeps its scenes. */
export function resetStyle(overrides: Overrides, key: string): Overrides {
  if (!overrides[key]) return overrides;
  const { [key]: removed, ...rest } = overrides;
  const kept = scenesOnly(removed);
  return Object.keys(kept).length ? { ...rest, [key]: kept } : rest;
}

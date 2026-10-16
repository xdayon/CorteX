import { expect, test } from "vitest";
import { defaultRenderSettings } from "../../editor/settings";
import { applySettingsEdit, deepMerge, diffSettings, effectiveSettings, isCustomized } from "./overrides";

const shared = defaultRenderSettings;

test("effective settings deep-merge the override over the shared settings and leave them untouched", () => {
  const effective = effectiveSettings(shared, { framing: { zoom: 2 }, captions: { font_size: 70 } });
  expect(effective.framing.zoom).toBe(2);
  expect(effective.framing.focus_mode).toBe("faces");
  expect(effective.captions).toMatchObject({ font_size: 70, style: "pop" });
  expect(shared.framing.zoom).toBe(1.45);
  expect(effectiveSettings(shared, undefined)).toBe(shared);
  expect(deepMerge({ a: [1, 2], b: { c: 1 } }, { a: [3], b: { d: 2 } })).toEqual({ a: [3], b: { c: 1, d: 2 } });
});

test("a clip edit stores only the differences and an edit back to the shared value clears the override", () => {
  const first = applySettingsEdit(shared, {}, "k", "clip", s => ({ ...s, framing: { ...s.framing, zoom: 2 } }));
  expect(first.shared).toBe(shared);
  expect(first.overrides).toEqual({ k: { framing: { zoom: 2 } } });
  expect(isCustomized(first.overrides, "k")).toBe(true);
  const second = applySettingsEdit(shared, first.overrides, "k", "clip", s => ({ ...s, captions: { ...s.captions, font_size: 70 } }));
  expect(second.overrides.k).toEqual({ framing: { zoom: 2 }, captions: { font_size: 70 } });
  const back = applySettingsEdit(shared, first.overrides, "k", "clip", s => ({ ...s, framing: { ...s.framing, zoom: shared.framing.zoom } }));
  expect(back.overrides).toEqual({});
});

test("an edit applied to all changes the shared settings and clears only the touched fields from the overrides", () => {
  const overrides = { a: { framing: { zoom: 2 }, captions: { font_size: 70 } }, b: { framing: { zoom: 1.2 } } };
  const result = applySettingsEdit(shared, overrides, "a", "all", s => ({ ...s, framing: { ...s.framing, zoom: 1.8 } }));
  expect(result.shared.framing.zoom).toBe(1.8);
  expect(result.overrides).toEqual({ a: { captions: { font_size: 70 } } });
  const unchanged = applySettingsEdit(shared, overrides, "a", "all", s => s);
  expect(unchanged.overrides).toBe(overrides);
});

test("diffSettings ignores equal arrays and reports changed ones whole", () => {
  expect(diffSettings({ a: { l: [1] }, b: 1 }, { a: { l: [1] }, b: 1 })).toEqual({});
  expect(diffSettings({ a: { l: [1] } }, { a: { l: [1, 2] } })).toEqual({ a: { l: [1, 2] } });
});

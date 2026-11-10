import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api } from "../../api";
import { defaultRunOptions, loadClipDefaults, resetClipDefaults } from "./runOptions";

vi.mock("../../api", async (original) => {
  const actual = await original<typeof import("../../api")>();
  return { ...actual, api: { ...actual.api, clipDefaults: vi.fn() } };
});

beforeEach(() => { resetClipDefaults(); vi.mocked(api.clipDefaults).mockReset(); });
afterEach(() => resetClipDefaults());

test("the server's clip defaults are loaded once and replace the fallback", async () => {
  vi.mocked(api.clipDefaults).mockResolvedValue({ count: 6, minimum_seconds: 50, maximum_seconds: 90 });
  expect(defaultRunOptions()).toMatchObject({ count: 10, minimum: 40, maximum: 120 });
  await loadClipDefaults();
  expect(defaultRunOptions()).toMatchObject({ count: 6, minimum: 50, maximum: 90 });
  await loadClipDefaults();
  expect(api.clipDefaults).toHaveBeenCalledTimes(1);
});

test("a failed request keeps the fallback and is retried next time", async () => {
  vi.mocked(api.clipDefaults).mockRejectedValueOnce(new Error("down")).mockResolvedValue({ count: 8, minimum_seconds: 45, maximum_seconds: 100 });
  await loadClipDefaults();
  expect(defaultRunOptions()).toMatchObject({ count: 10, minimum: 40, maximum: 120 });
  await loadClipDefaults();
  expect(defaultRunOptions()).toMatchObject({ count: 8, minimum: 45, maximum: 100 });
});

import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { api, type Publication } from "./api";
import { PublicationBadges, PublicationPanel } from "./PublicationPanel";
import { buildPublicationPayload, emptyPublicationForm, formatViews } from "./publicationForm";

vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
  return { ...actual, api: { ...actual.api, savePublication: vi.fn(), deletePublication: vi.fn() } };
});

const saved: Publication = {
  id: "p", project_id: "proj", render_artifact_id: "art", title: "T", headline: "H", hook_text: "G",
  duration_seconds: 60, published: true, platforms: ["instagram"], published_on: "2026-10-01",
  url: null, views: 12300, likes: null, comments: null, shares: 40, saves: 10,
  retention_3s_pct: 71.5, avg_watch_seconds: null, rating: 1, notes: "",
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z",
};

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

test("payload converts text fields, commas and rating", () => {
  const parsed = buildPublicationPayload({
    ...emptyPublicationForm, published: true, platforms: ["tiktok"], published_on: "2026-10-02",
    url: " https://tiktok.com/x ", views: "1500", shares: "30", retention_3s_pct: "72,5",
    avg_watch_seconds: "18.2", rating: "-1", notes: "ok",
  }, "gancho");
  expect(parsed).toEqual({ ok: true, payload: {
    published: true, platforms: ["tiktok"], published_on: "2026-10-02", url: "https://tiktok.com/x",
    views: 1500, likes: null, comments: null, shares: 30, saves: null,
    retention_3s_pct: 72.5, avg_watch_seconds: 18.2, rating: -1, notes: "ok", hook_text: "gancho",
  } });
});

test("invalid numbers and links are rejected with Portuguese messages", () => {
  const parsed = buildPublicationPayload({
    ...emptyPublicationForm, views: "-3", likes: "1,5", retention_3s_pct: "120", avg_watch_seconds: "abc",
    url: "ftp://x.com",
  });
  expect(parsed.ok).toBe(false);
  if (!parsed.ok) expect(parsed.errors).toHaveLength(5);
});

test("formatViews abbreviates thousands and millions", () => {
  expect([formatViews(950), formatViews(12300), formatViews(2_500_000)]).toEqual(["950", "12,3 mil", "2,5 mi"]);
});

test("the form blocks invalid input and sends the payload when valid", async () => {
  vi.mocked(api.savePublication).mockResolvedValue(saved);
  const onSaved = vi.fn();
  render(<PublicationPanel projectId="proj" artifactId="art" hookText="Gancho" onSaved={onSaved} onDeleted={vi.fn()}/>);
  fireEvent.change(screen.getByLabelText("Views"), { target: { value: "abc" } });
  fireEvent.click(screen.getByText("Salvar publicação"));
  expect(screen.getByRole("alert").textContent).toContain("Views");
  expect(api.savePublication).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Views"), { target: { value: "2000" } });
  fireEvent.click(screen.getByLabelText("Publicado"));
  fireEvent.click(screen.getByLabelText("Instagram"));
  fireEvent.change(screen.getByLabelText("Nota"), { target: { value: "1" } });
  fireEvent.click(screen.getByText("Salvar publicação"));
  await waitFor(() => expect(onSaved).toHaveBeenCalledWith(saved));
  expect(api.savePublication).toHaveBeenCalledWith("proj", "art", expect.objectContaining({
    published: true, platforms: ["instagram"], views: 2000, rating: 1, hook_text: "Gancho",
  }));
});

test("badges show published views and rating, and nothing without a record", () => {
  const { container, rerender } = render(<PublicationBadges publication={saved}/>);
  expect(container.textContent).toContain("publicado");
  expect(container.textContent).toContain("12,3 mil views");
  expect(container.textContent).toContain("nota: ótimo");
  rerender(<PublicationBadges publication={null}/>);
  expect(container.textContent).toBe("");
});

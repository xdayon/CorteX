import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { VoiceSelector } from "./VoiceSelector";
import { api, type EpisodeEntry } from "./api";

vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
  return { ...actual, api: {
    ...actual.api,
    episodeVoices: vi.fn(), selectEpisodeVoice: vi.fn(),
  } };
});

const entry: EpisodeEntry = {
  id: "AbCdEfGh_12", url: "https://www.youtube.com/watch?v=AbCdEfGh_12",
  title: "Podcast com Dayon", project_ids: ["project"], participant_count: 3,
  primary_subject: "Dayon", subject_reference: {}, diarizations: [],
  source: { id: "source", project_id: "project", kind: "youtube", stored_path: "source.mp4", sha256: "fixture", size_bytes: 1 },
  source_bytes: 1, runs: [], jobs: [], renders: [],
};
async function settle() { await act(async () => {}); }
function openSelector(episode = entry, disabled = false) {
  const view = render(<VoiceSelector entry={episode} disabled={disabled} />);
  const details = view.container.querySelector("details")!;
  act(() => { details.open = true; details.dispatchEvent(new Event("toggle")); });
  return view;
}

beforeEach(() => {
  vi.resetAllMocks();
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

test("an episode without detected voices offers nothing to confirm", async () => {
  openSelector();
  await settle();
  expect(screen.getByText(/Ainda sem vozes neste episódio/)).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Ouvir vozes detectadas" })).toBeNull();
});

test("already persisted voice samples can be opened and confirmed by the user", async () => {
  vi.mocked(api.episodeVoices).mockResolvedValue({ turns: [{ start: 4, end: 8, speaker: "SPEAKER_01" }] });
  openSelector({ ...entry, diarizations: [{ id: "voices", project_id: "project" }] });
  fireEvent.click(screen.getByRole("button", { name: "Ouvir vozes detectadas" }));
  await settle();
  expect(api.episodeVoices).toHaveBeenCalledWith(entry.id, "voices");
  fireEvent.click(screen.getByRole("button", { name: "Esta voz é minha" }));
  await settle();
  expect(api.selectEpisodeVoice).toHaveBeenCalledWith(entry.id, "voices", "SPEAKER_01");
  expect(screen.getByRole("button", { name: "Dayon confirmado" })).toBeTruthy();
});

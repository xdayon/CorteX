import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import { EpisodeLibrary } from "./EpisodeLibrary";
import { api, type EpisodeEntry } from "./api";

vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
  return { ...actual, api: {
    ...actual.api,
    episodes: vi.fn(), refreshEpisodeMetadata: vi.fn(), archiveEpisode: vi.fn(),
    downloadEpisode: vi.fn(),
  } };
});

const episode: EpisodeEntry = {
  id: "AbCdEfGh_12", url: "https://www.youtube.com/watch?v=AbCdEfGh_12",
  title: "Consciência e sociedade com Dayon", project_ids: ["project-one"],
  channel_name: "Podcast Horizonte", channel_url: "https://www.youtube.com/@horizonte",
  thumbnail_url: "https://i.ytimg.com/vi/AbCdEfGh_12/hqdefault.jpg",
  metadata_updated_at: "2026-09-11T10:00:00+00:00", metadata_error: null,
  participant_count: 3, primary_subject: "Dayon", archived: false,
  subject_reference: {}, diarizations: [], source: null, source_bytes: 0,
  runs: [], jobs: [], renders: [],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((yes) => { resolve = yes; });
  return { promise, resolve };
}

async function settle() { await act(async () => {}); }

function openLibrary() {
  const onOpen = vi.fn();
  const onUse = vi.fn();
  const view = render(<EpisodeLibrary busy={false} onOpen={onOpen} onUse={onUse} />);
  return { ...view, onOpen, onUse };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  vi.mocked(api.episodes).mockResolvedValue([episode]);
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

test("loading never displays an empty catalogue before the request completes", async () => {
  const pending = deferred<EpisodeEntry[]>();
  vi.mocked(api.episodes).mockReturnValueOnce(pending.promise);
  openLibrary();
  expect(screen.getByRole("status").textContent).toContain("Carregando episódios");
  expect(screen.queryByText(/Adicione um episódio pelo botão/)).toBeNull();
  await act(async () => pending.resolve([]));
  expect(screen.queryByRole("status")).toBeNull();
  expect(screen.getByText(/Adicione um episódio pelo botão/)).toBeTruthy();
});

test("the card shows persisted title channel and cover and passes the canonical episode to onUse", async () => {
  const { onUse } = openLibrary();
  await settle();
  expect(screen.getByRole("heading", { name: episode.title }).tagName).toBe("H3");
  expect(screen.getByRole("link", { name: episode.channel_name! }).getAttribute("href")).toBe(episode.channel_url);
  expect(screen.getByRole("img", { name: `Capa de ${episode.title}` }).getAttribute("src")).toBe(episode.thumbnail_url);
  expect(screen.getByText(/3 participantes/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Preparar cortes" }));
  expect(onUse).toHaveBeenCalledWith(episode);
  expect(onUse.mock.calls[0][0].url).toBe("https://www.youtube.com/watch?v=AbCdEfGh_12");
  expect(api.downloadEpisode).not.toHaveBeenCalled();
  expect(api.refreshEpisodeMetadata).not.toHaveBeenCalled();
});

test("search filters by title or channel locally and reports no matches", async () => {
  const second: EpisodeEntry = { ...episode, id: "ZyXwVuTs_98", title: "Outro encontro", channel_name: "Canal Terra" };
  vi.mocked(api.episodes).mockResolvedValue([episode, second]);
  openLibrary();
  await settle();
  const search = screen.getByRole("searchbox", { name: "Buscar episódio ou canal" });
  fireEvent.change(search, { target: { value: "CONSCIÊNCIA" } });
  expect(screen.getByRole("heading", { name: episode.title })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: second.title })).toBeNull();
  fireEvent.change(search, { target: { value: "canal terra" } });
  expect(screen.getByRole("heading", { name: second.title })).toBeTruthy();
  expect(screen.queryByRole("heading", { name: episode.title })).toBeNull();
  fireEvent.change(search, { target: { value: "inexistente" } });
  expect(screen.getByText("Nenhum episódio corresponde à busca.")).toBeTruthy();
  expect(api.episodes).toHaveBeenCalledTimes(1);
});

test("metadata only refreshes after a click and shows progress until saved data arrives", async () => {
  const pending = deferred<EpisodeEntry>();
  vi.mocked(api.refreshEpisodeMetadata).mockReturnValueOnce(pending.promise);
  openLibrary();
  await settle();
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(api.refreshEpisodeMetadata).not.toHaveBeenCalled();
  const button = screen.getByRole("button", { name: "Atualizar dados do YouTube" });
  fireEvent.click(button);
  expect(api.refreshEpisodeMetadata).toHaveBeenCalledWith(episode.id);
  expect((button as HTMLButtonElement).disabled).toBe(true);
  expect(screen.getByRole("status").textContent).toContain("Buscando título, canal e capa");
  const updated = { ...episode, title: "Título atualizado pelo YouTube" };
  vi.mocked(api.episodes).mockResolvedValue([updated]);
  await act(async () => pending.resolve(updated));
  expect(screen.getByRole("heading", { name: updated.title })).toBeTruthy();
  expect(screen.queryByRole("status")).toBeNull();
  expect(api.refreshEpisodeMetadata).toHaveBeenCalledTimes(1);
});

test("a persisted metadata error stays visible while the episode remains usable", async () => {
  const failed = { ...episode, metadata_error: "YouTube indisponível" };
  vi.mocked(api.refreshEpisodeMetadata).mockResolvedValue(failed);
  openLibrary();
  await settle();
  vi.mocked(api.episodes).mockResolvedValue([failed]);
  fireEvent.click(screen.getByRole("button", { name: "Atualizar dados do YouTube" }));
  await settle();
  expect(screen.getByRole("alert").textContent).toContain("YouTube indisponível");
  expect(screen.getByRole("heading", { name: episode.title })).toBeTruthy();
  expect((screen.getByRole("button", { name: "Preparar cortes" }) as HTMLButtonElement).disabled).toBe(false);
});

test("archive hides the episode and showing archived entries allows restoration", async () => {
  let stored = { ...episode };
  vi.mocked(api.episodes).mockImplementation(async (includeArchived) => includeArchived || !stored.archived ? [stored] : []);
  vi.mocked(api.archiveEpisode).mockImplementation(async (_id, archived) => {
    stored = { ...stored, archived };
    return stored;
  });
  openLibrary();
  await settle();
  fireEvent.click(screen.getByRole("button", { name: "Arquivar" }));
  await settle();
  expect(api.archiveEpisode).toHaveBeenCalledWith(episode.id, true);
  expect(screen.queryByRole("heading", { name: episode.title })).toBeNull();
  fireEvent.click(screen.getByRole("checkbox", { name: "Mostrar arquivados" }));
  await settle();
  expect(api.episodes).toHaveBeenLastCalledWith(true);
  expect(screen.getByText("Arquivado")).toBeTruthy();
  expect((screen.getByRole("button", { name: "Preparar cortes" }) as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Restaurar episódio" }));
  await settle();
  expect(api.archiveEpisode).toHaveBeenLastCalledWith(episode.id, false);
  expect(screen.queryByText("Arquivado")).toBeNull();
  expect((screen.getByRole("button", { name: "Preparar cortes" }) as HTMLButtonElement).disabled).toBe(false);
});

test("an unavailable cover displays a placeholder without losing the episode", async () => {
  openLibrary();
  await settle();
  fireEvent.error(screen.getByRole("img", { name: `Capa de ${episode.title}` }));
  expect(screen.queryByRole("img")).toBeNull();
  expect(screen.getByText("Capa ainda não disponível")).toBeTruthy();
  expect(screen.getByRole("heading", { name: episode.title })).toBeTruthy();
});

test('an idle library does not poll and refreshes when the window regains focus', async () => {
  openLibrary(); await settle();
  await act(async () => vi.advanceTimersByTime(15000));
  expect(api.episodes).toHaveBeenCalledTimes(1);
  fireEvent(window, new Event('focus')); await settle();
  expect(api.episodes).toHaveBeenCalledTimes(2);
});

test('polls active jobs while visible and stops after completion', async () => {
  vi.mocked(api.episodes).mockResolvedValueOnce([{...episode,jobs:[{id:'job',status:'running'}]}]).mockResolvedValue([episode]);
  openLibrary(); await settle();
  await act(async () => vi.advanceTimersByTime(5000));
  expect(api.episodes).toHaveBeenCalledTimes(2);
  await act(async () => vi.advanceTimersByTime(15000));
  expect(api.episodes).toHaveBeenCalledTimes(2);
});

test('an obsolete filter response cannot replace the current catalogue', async () => {
  const older = deferred<EpisodeEntry[]>();
  vi.mocked(api.episodes).mockReturnValueOnce(older.promise).mockResolvedValue([{...episode,archived:true}]);
  openLibrary();
  fireEvent.click(screen.getByRole('checkbox',{name:'Mostrar arquivados'})); await settle();
  expect(screen.getByText('Arquivado')).toBeTruthy();
  await act(async () => older.resolve([]));
  expect(screen.getByText('Arquivado')).toBeTruthy();
});

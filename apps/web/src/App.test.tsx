import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, test, vi } from "vitest";
import App from "./App";
import { api, type ApiJob, type WorkflowRun } from "./api";

vi.mock("./api", async (original) => {
  const actual = await original<typeof import("./api")>();
  return { ...actual, api: Object.fromEntries(Object.entries(actual.api).map(([name, fn]) => [
    name, vi.fn(name.endsWith("Url") ? fn : undefined),
  ])) };
});

const ready: WorkflowRun = {
  id: "run", project_id: "project", source_asset_id: "source", status: "ready_for_review",
  stage: "review", progress: 100, message: "Pronto", brief: { count: 10, minimum_seconds: 40, maximum_seconds: 120 },
  artifacts: { transcript: "transcript", analysis: "analysis", suggestion: "suggestion" },
};
const visualRun: WorkflowRun = {
  ...ready, subject_identity_id: "guest",
  artifacts: { ...ready.artifacts, scene_index: "scene", face_index: "face", speaker_timeline: "speaker",
    camera_timeline: "camera", visual_quality: "quality", identity_index: "identity" },
};
const suggestion = { document: { selection: {
  schema_version: "1.0", selection_notes: "Sugestões reais", clips: [{
    rank: 1, title: "Uma ideia", headline: "Headline real", start_second: 0, end_second: 40,
    estimated_duration: 40, pacing: "balanced", primary_speaker: "Convidado", reasoning: "Boa ideia",
  }],
} } } as Awaited<ReturnType<typeof api.suggestion>>;

function job(id: string, result: Record<string, unknown> = {}): ApiJob {
  return { id, type: "render", status: "succeeded", progress: 100, message: "Pronto", result } as ApiJob;
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (reason: Error) => void;
  const promise = new Promise<T>((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
async function settle() { await act(async () => {}); }
async function resumeLastEpisode() {
  await settle();
  fireEvent.click(screen.getByRole("button", {name: "Retomar último episódio"}));
  await settle();
}
async function openExport(saved = ready, automatic = true) {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValue(saved);
  const view = render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByRole("button", { name: /Editar 1 selecionado/ }));
  if (automatic) fireEvent.change(screen.getByRole("combobox", { name: "Composição" }), { target: { value: "speaker_auto" } });
  return view;
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout"] });
  localStorage.clear();
  vi.mocked(api.episodes).mockResolvedValue([]);
  vi.mocked(api.suggestion).mockResolvedValue(suggestion);
  vi.mocked(api.identityIndex).mockResolvedValue({ document: { identities: [
    { identity_id: "guest", status: "confirmed" },
    { identity_id: "host", status: "confirmed" },
    { identity_id: "third", status: "confirmed" },
  ] } } as Awaited<ReturnType<typeof api.identityIndex>>);
  vi.mocked(api.startEditPlan).mockResolvedValue(job("plan"));
  vi.mocked(api.startRender).mockResolvedValue(job("render"));
  vi.mocked(api.cameraTimeline).mockResolvedValue({ document: { scenes: [
    { scene_index: 0, start_us: 0, end_us: 40_000_000, role: "two_shot" },
  ] } } as Awaited<ReturnType<typeof api.cameraTimeline>>);
  vi.mocked(api.renderArtifact).mockResolvedValue({ document: { auto_framing: [] } } as unknown as Awaited<ReturnType<typeof api.renderArtifact>>);
  vi.mocked(api.startSceneIndex).mockResolvedValue(job("scene"));
  vi.mocked(api.startFaceIndex).mockResolvedValue(job("face"));
  vi.mocked(api.startSpeakerTimeline).mockResolvedValue(job("speaker"));
  vi.mocked(api.startCameraTimeline).mockResolvedValue(job("timeline"));
  vi.mocked(api.startVisualQuality).mockResolvedValue(job("quality"));
  vi.mocked(api.startIdentityIndex).mockResolvedValue(job("identity"));
  vi.mocked(api.startReactionCandidates).mockResolvedValue(job("reaction"));
  vi.mocked(api.startCameraPlan).mockResolvedValue(job("camera"));
  vi.mocked(api.watchJob).mockImplementation(async (id) => job(id, {
    edit_plan_artifact_id: "edl", render_artifact_id: "mp4", reaction_candidate_artifact_id: "reaction",
    camera_edit_plan_artifact_id: "camera-plan",
    scene_index_artifact_id: "scene", face_index_artifact_id: "face",
    speaker_timeline_artifact_id: "speaker", camera_timeline_artifact_id: "camera",
    visual_quality_artifact_id: "quality", identity_index_artifact_id: "identity",
  }));
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); });

test.each(["queued", "running"] as const)("reopening %s work follows it to human review", async (status) => {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValueOnce({ ...ready, status, stage: "analyze", progress: 35, message: "Analisando" }).mockResolvedValueOnce(ready);
  render(<App />);
  await resumeLastEpisode();
  expect(screen.getByText("Analisando")).toBeTruthy();
  expect((screen.getByRole("button", { name: /Processando episódio/ }) as HTMLButtonElement).disabled).toBe(true);
  await act(() => vi.advanceTimersByTimeAsync(1200));
  expect(screen.getByText("Uma ideia")).toBeTruthy();
  expect(api.workflowRun).toHaveBeenCalledTimes(2);
  expect(api.startRender).not.toHaveBeenCalled();
});

test("unmount cancels polling and ignores a late response in a replacement view", async () => {
  localStorage.setItem("cortex-active-run", "project:run");
  const pending = deferred<WorkflowRun>();
  vi.mocked(api.workflowRun).mockResolvedValueOnce({ ...ready, status: "running" }).mockReturnValueOnce(pending.promise);
  const first = render(<App />);
  await resumeLastEpisode();
  await act(() => vi.advanceTimersByTimeAsync(1200));
  const signal = vi.mocked(api.workflowRun).mock.calls[1][2]!;
  first.unmount();
  expect(signal.aborted).toBe(true);
  localStorage.removeItem("cortex-active-run");
  render(<App />);
  await act(async () => { pending.resolve(ready); });
  await act(() => vi.advanceTimersByTimeAsync(5000));
  expect(api.workflowRun).toHaveBeenCalledTimes(2);
  expect(api.suggestion).not.toHaveBeenCalled();
  expect(screen.queryByText("Uma ideia")).toBeNull();
});

test.each([
  ["failed", "Codex indisponível"], ["cancelled", "O processamento foi cancelado"],
] as const)("reopening %s work displays its terminal failure", async (status, message) => {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValue({ ...ready, status, error: status === "failed" ? message : null });
  render(<App />);
  await resumeLastEpisode();
  expect(screen.getByRole("alert").textContent).toContain(message);
  expect(api.suggestion).not.toHaveBeenCalled();
});

test("a temporary reload error keeps the saved run available for retry", async () => {
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockRejectedValueOnce(new Error("API indisponível"));
  render(<App />);
  await resumeLastEpisode();
  expect(screen.getByRole("alert").textContent).toContain("API indisponível");
  expect(localStorage.getItem("cortex-active-run")).toBe("project:run");
});

test("old subject selection does not select an interviewer or request reactions", async () => {
  await openExport(visualRun);
  expect(screen.getByRole("button", { name: "Selecionar entrevistador guest" }).getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.startReactionCandidates).not.toHaveBeenCalled();
  expect(api.startCameraPlan).toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "Baixar MP4" }).getAttribute("href")).toContain("/renders/mp4/media");
  expect(screen.getByRole("link", { name: "Baixar SRT" }).getAttribute("href")).toContain("/renders/mp4/subtitles");
});

test("interviewer selection waits for persistence and uses the chosen person among three faces", async () => {
  const saved = deferred<WorkflowRun>();
  vi.mocked(api.selectWorkflowIdentity).mockReturnValueOnce(saved.promise);
  await openExport(visualRun);
  const choice = screen.getByRole("button", { name: "Selecionar entrevistador third" });
  fireEvent.click(choice);
  expect(choice.getAttribute("aria-pressed")).toBe("false");
  expect((screen.getByRole("button", { name: "Renderizar 1 corte" }) as HTMLButtonElement).disabled).toBe(true);
  await act(async () => { saved.resolve({ ...visualRun, interviewer_identity_id: "third" }); });
  expect(choice.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Reaproveitar reações de outro instante" }));
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.startReactionCandidates).toHaveBeenCalledWith("project", "speaker", "camera", "identity", "quality", "third");
});

test("failed identity persistence keeps the previously confirmed choice", async () => {
  vi.mocked(api.selectWorkflowIdentity).mockRejectedValueOnce(new Error("Falha ao salvar"));
  await openExport({ ...visualRun, interviewer_identity_id: "host" });
  fireEvent.click(screen.getByRole("button", { name: "Selecionar entrevistador third" }));
  await settle();
  expect(screen.getByRole("button", { name: "Selecionar entrevistador host" }).getAttribute("aria-pressed")).toBe("true");
  expect(screen.getByRole("alert").textContent).toContain("Falha ao salvar");
});

test("camera failures remain visible when full frame render is explicitly selected", async () => {
  vi.mocked(api.watchJob).mockImplementation(async (id) => {
    if (id === "reaction" || id === "camera") return { ...job(id), status: "failed", error: `${id} indisponível` };
    return job(id, { edit_plan_artifact_id: "edl", render_artifact_id: "mp4" });
  });
  await openExport({ ...visualRun, interviewer_identity_id: "host" });
  fireEvent.change(screen.getByRole("combobox", { name: "Composição" }), { target: { value: "blurred_background" } });
  fireEvent.click(screen.getByRole("button", { name: "Reaproveitar reações de outro instante" }));
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(screen.getByRole("status", { name: "Avisos de câmera" }).textContent).toContain("Render continuará sem reações: reaction indisponível");
  expect(screen.getByRole("status", { name: "Avisos de câmera" }).textContent).toContain("seguirá sem plano de câmeras: camera indisponível");
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.objectContaining({
    headline: expect.objectContaining({ text: "Headline real" }),
  }), undefined);
  expect(screen.getByRole("link", { name: "Baixar MP4" })).toBeTruthy();
});

test("unavailable optional artifacts do not prevent restoring suggestions", async () => {
  vi.mocked(api.identityIndex).mockRejectedValueOnce(new Error("Arquivo ausente"));
  await openExport(visualRun);
  expect(screen.getByRole("status", { name: "Avisos de câmera" }).textContent).toContain("Arquivo ausente");
  expect(screen.getByRole("button", { name: "Renderizar 1 corte" })).toBeTruthy();
});

test("starting another episode clears previous selection, settings, camera state and downloads", async () => {
  await openExport({ ...visualRun, interviewer_identity_id: "host" });
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "72" } });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(screen.getByRole("link", { name: "Baixar MP4" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: /Novo episódio/ }));
  fireEvent.click(screen.getByRole("button", { name: "Arquivo" }));
  const input = document.querySelector('input[type="file"]')!;
  fireEvent.change(input, { target: { files: [new File(["video"], "next.mp4", { type: "video/mp4" })] } });
  expect(api.createProject).not.toHaveBeenCalled();
  vi.mocked(api.createProject).mockResolvedValue({ id: "next-project" } as Awaited<ReturnType<typeof api.createProject>>);
  vi.mocked(api.uploadSource).mockResolvedValue({ id: "next-source" } as Awaited<ReturnType<typeof api.uploadSource>>);
  const pending = deferred<WorkflowRun>();
  vi.mocked(api.createWorkflowRun).mockReturnValueOnce(pending.promise);
  fireEvent.click(screen.getByRole("button", { name: "Processar episódio" }));
  await settle();
  expect((screen.getByRole("button", { name: /Escolher cortes/ }) as HTMLButtonElement).disabled).toBe(true);
  expect(localStorage.getItem("cortex-active-run")).toBeNull();
  await act(async () => { pending.resolve({ ...ready, id: "next-run", project_id: "next-project", source_asset_id: "next-source" }); });
  fireEvent.click(screen.getByRole("button", { name: /Editar 1 selecionado/ }));
  expect(screen.queryByRole("link", { name: "Baixar MP4" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Selecionar entrevistador host" })).toBeNull();
  expect((screen.getByRole("slider", { name: /Tamanho/ }) as HTMLInputElement).value).toBe("56");
  expect(localStorage.getItem("cortex-active-run")).toBe("next-project:next-run");
});

test("only reviewed selections render, with the chosen caption and headline settings", async () => {
  vi.mocked(api.suggestion).mockResolvedValueOnce({ ...suggestion, document: {
    ...suggestion.document, selection: { ...suggestion.document.selection, clips: [
      suggestion.document.selection.clips[0],
      { ...suggestion.document.selection.clips[0], rank: 2, title: "Outra ideia", start_second: 60, end_second: 100 },
    ] },
  } });
  localStorage.setItem("cortex-active-run", "project:run");
  vi.mocked(api.workflowRun).mockResolvedValue(ready);
  render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByText("Uma ideia"));
  fireEvent.click(screen.getByRole("button", { name: /Editar 1 selecionado/ }));
  fireEvent.change(screen.getByRole("slider", { name: /Tamanho/ }), { target: { value: "64" } });
  fireEvent.change(document.querySelector('input[type="color"]')!, { target: { value: "#ffcc00" } });
  fireEvent.click(screen.getByRole("button", { name: "Mostrar headline" }));
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.startEditPlan).toHaveBeenCalledExactlyOnceWith("project", "transcript", "analysis", 60, 100, "balanced");
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.objectContaining({
    captions: expect.objectContaining({ font_size: 64, text_color: "#FFCC00" }),
    headline: expect.objectContaining({ enabled: false }),
    framing: expect.objectContaining({ mode: "blurred_background" }),
  }), undefined);
  expect(screen.getByRole("heading", {name:"Outra ideia"})).toBeTruthy();
  expect(screen.queryByText("Uma ideia")).toBeNull();
});


test("automatic framing prepares cameras without requiring an interviewer identity", async () => {
  await openExport();
  fireEvent.click(screen.getByRole("button", { name: "Zoom discreto alternado (1,15×)" }));
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.startSceneIndex).toHaveBeenCalledOnce();
  expect(api.startSpeakerTimeline).toHaveBeenCalledOnce();
  expect(api.startReactionCandidates).not.toHaveBeenCalled();
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.objectContaining({
    framing: expect.objectContaining({ mode: "speaker_auto", punch_in: expect.objectContaining({ enabled: true }) }),
  }), "camera-plan");
});

test("automatic framing does not silently switch to center crop after camera failure", async () => {
  await openExport(visualRun);
  vi.mocked(api.startCameraPlan).mockRejectedValueOnce(new Error("Análise indisponível"));
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.startRender).not.toHaveBeenCalled();
  expect(screen.getByRole("alert").textContent).toContain("Análise indisponível");
});

test("context fallback decisions from the persisted render are visible", async () => {
  await openExport(visualRun);
  vi.mocked(api.renderArtifact).mockResolvedValueOnce({ document: { auto_framing: [
    { mode: "blurred_background", reason: "speaker_uncertain_keep_context" },
  ] } } as Awaited<ReturnType<typeof api.renderArtifact>>);
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(screen.getByRole("status", { name: "Avisos de câmera" }).textContent).toContain("1 planos mantiveram o quadro inteiro");
});


test("manual scene correction reaches the persisted render request", async () => {
  await openExport(visualRun);
  fireEvent.click(screen.getByText(/Ajustar enquadramento por câmera/));
  fireEvent.change(screen.getByRole("combobox", { name: "Enquadramento do plano 1" }), { target: { value: "right" } });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.objectContaining({
    framing: expect.objectContaining({ scene_overrides: [{ scene_index: 0, target: "right" }] }),
  }), "camera-plan");
});

 test("default export skips episode-wide visual analysis", async () => {
  await openExport(ready, false);
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(api.startFaceIndex).not.toHaveBeenCalled();
  expect(api.startSceneIndex).not.toHaveBeenCalled();
  expect(api.startCameraPlan).not.toHaveBeenCalled();
  expect(api.startRender).toHaveBeenCalledWith("project", "edl", expect.objectContaining({framing: expect.objectContaining({mode: "blurred_background"})}), undefined);
});

test("visual preparation displays server progress instead of render zero", async () => {
  await openExport();
  const pending = deferred<ApiJob>();
  vi.mocked(api.watchJob).mockImplementation(async (id, onUpdate) => {
    onUpdate?.({ ...job(id), status: "running", progress: 37, message: "Rostos: 500/1400 amostras" });
    return pending.promise;
  });
  fireEvent.click(screen.getByRole("button", { name: "Renderizar 1 corte" }));
  await settle();
  expect(screen.getByText("37%")).toBeTruthy();
  expect(screen.getAllByText("Rostos: 500/1400 amostras").length).toBeGreaterThan(0);
});

test("caption controls update the preview and the render payload", async () => {
  await openExport(ready, false);
  fireEvent.change(screen.getByRole("combobox", {name:"Fonte"}), {target:{value:"Lato"}});
  fireEvent.click(screen.getByRole("checkbox", {name:"Negrito"}));
  fireEvent.click(screen.getByRole("checkbox", {name:"Caixa alta"}));
  fireEvent.change(screen.getByRole("slider", {name:"Posição vertical"}), {target:{value:"60"}});
  fireEvent.change(screen.getByRole("slider", {name:/Tamanho/}), {target:{value:"64"}});
  const preview = screen.getByLabelText("Prévia da legenda em 1080 por 1920");
  expect(preview.textContent).toContain("Uma");
  expect(preview.querySelector('[style*="font-weight: 400"]')?.getAttribute("style")).toContain("font-weight: 400");
  fireEvent.click(screen.getByRole("button", {name:"Renderizar 1 corte"}));
  await settle();
  expect(api.startRender).toHaveBeenCalledWith("project","edl",expect.objectContaining({captions:expect.objectContaining({font_family:"Lato",font_weight:400,uppercase:false,position_y:.6,font_size:64})}),undefined);
});

test("starting another episode preserves a resumable draft without deleting artifacts", async () => {
  await openExport(ready, false);
  fireEvent.change(screen.getByRole("slider", {name:/Tamanho/}), {target:{value:"64"}});
  fireEvent.click(screen.getByRole("button", {name:/1Novo episódio/}));
  expect(localStorage.getItem("cortex-active-run")).toBeNull();
  expect(screen.getByPlaceholderText("https://youtube.com/watch?v=...")).toBeTruthy();
  expect(screen.queryByRole("button", {name:"Renderizar 1 corte"})).toBeNull();
  expect(localStorage.getItem("cortex-draft:project:run")).toContain('"font_size":64');
  fireEvent.click(screen.getByRole("button", {name:"Uma ideia"}));
  await settle();
  fireEvent.click(screen.getByRole("button", {name:/Editar 1 selecionado/}));
  expect((screen.getByRole("slider", {name:/Tamanho/}) as HTMLInputElement).value).toBe("64");
  expect(localStorage.getItem("cortex-active-run")).toBe("project:run");
});


test("real preview limits rendering to ten seconds and keeps final exports separate", async () => {
  await openExport(ready, false);
  fireEvent.click(screen.getByRole("button", {name:"Gerar prévia curta (~10 s)"}));
  await settle();
  expect(api.startEditPlan).toHaveBeenCalledExactlyOnceWith("project","transcript","analysis",0,10,"balanced");
  expect(screen.getByText("Prévia · Uma ideia")).toBeTruthy();
  expect(screen.queryByRole("link",{name:"Baixar MP4"})).toBeNull();
});

test("automatic analysis sends only the selected clip ranges with context", async () => {
  await openExport();
  fireEvent.click(screen.getByRole("button", {name:"Renderizar 1 corte"}));
  await settle();
  expect(api.startSceneIndex).toHaveBeenCalledWith("project","source",[{start:0,end:43}]);
});

test("caption corrections are saved per clip and reach preview rendering", async () => {
  vi.mocked(api.transcript).mockResolvedValue({document:{segments:[{words:[{word:"Cortecs",start:1,end:2}]}]}});
  await openExport(ready,false);
  const details = document.querySelector('.transcript-editor') as HTMLDetailsElement;
  details.open=true; fireEvent(details,new Event('toggle'));
  await settle();
  fireEvent.change(screen.getByRole("textbox", {name:"Palavra 1: Cortecs"}),{target:{value:"CorteX"}});
  expect(localStorage.getItem("cortex-draft:project:run")).toContain('CorteX');
  fireEvent.click(screen.getByRole("button",{name:"Gerar prévia curta (~10 s)"}));
  await settle();
  expect(api.startRender).toHaveBeenCalledWith("project","edl",expect.objectContaining({captions:expect.objectContaining({corrections:[{word_index:0,original:"Cortecs",text:"CorteX"}]})}),undefined);
});

test("autosave restores settings, corrections, headline and completed exports after a reload without reset", async () => {
  vi.mocked(api.transcript).mockResolvedValue({document:{segments:[{words:[{word:"Cortecs",start:1,end:2}]}]}});
  const view = await openExport(ready, false);
  fireEvent.change(screen.getByRole("slider", {name:/Tamanho/}), {target:{value:"72"}});
  fireEvent.change(screen.getByRole("slider", {name:"Posição vertical"}), {target:{value:"65"}});
  fireEvent.change(screen.getByRole("textbox", {name:/Headline manual/}), {target:{value:"Minha headline"}});
  const details = document.querySelector('.transcript-editor') as HTMLDetailsElement;
  details.open = true; fireEvent(details, new Event('toggle'));
  await settle();
  fireEvent.change(screen.getByRole("textbox", {name:"Palavra 1: Cortecs"}), {target:{value:"CorteX"}});
  fireEvent.click(screen.getByRole("button", {name:"Renderizar 1 corte"}));
  await settle();
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!)).toMatchObject({
    schema_version:1, settings:{captions:{font_size:72,position_y:.65}},
    captionEdits:{"1:0:40:balanced":[{word_index:0,original:"Cortecs",text:"CorteX"}]},
    headlines:{"1:0:40:balanced":"Minha headline"}, selectedKeys:["1:0:40:balanced"],
  });
  view.unmount();
  render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByRole("button", {name:/Editar 1 selecionado/}));
  expect((screen.getByRole("slider", {name:/Tamanho/}) as HTMLInputElement).value).toBe("72");
  expect((screen.getByRole("slider", {name:"Posição vertical"}) as HTMLInputElement).value).toBe("65");
  expect((screen.getByRole("textbox", {name:/Headline manual/}) as HTMLTextAreaElement).value).toBe("Minha headline");
  expect(screen.getByRole("link", {name:"Baixar MP4"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button", {name:"Gerar prévia curta (~10 s)"}));
  await settle();
  expect(vi.mocked(api.startRender).mock.lastCall?.[2]).toMatchObject({
    headline:{text:"Minha headline"}, captions:{corrections:[{word_index:0,original:"Cortecs",text:"CorteX"}]},
  });
});

test("headlines are independent per clip, apply to preview and batch, and explicitly restore the AI suggestion", async () => {
  const multiple = structuredClone(suggestion);
  multiple.document.selection.clips.push({...multiple.document.selection.clips[0], rank:2, title:"Outra ideia", headline:"Outra sugestão", start_second:60, end_second:100});
  vi.mocked(api.suggestion).mockResolvedValue(multiple);
  vi.mocked(api.workflowRun).mockResolvedValue(ready);
  localStorage.setItem("cortex-active-run", "project:run");
  const view = render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByRole("button", {name:/Editar 2 selecionados/}));
  fireEvent.change(screen.getByRole("textbox", {name:/Headline manual/}), {target:{value:"Headline do primeiro corte"}});
  fireEvent.click(screen.getByRole("button", {name:"Gerar prévia curta (~10 s)"}));
  await settle();
  expect(vi.mocked(api.startRender).mock.lastCall?.[2].headline.text).toBe("Headline do primeiro corte");
  fireEvent.change(screen.getByRole("combobox", {name:"Corte em revisão"}), {target:{value:"2:60:100:balanced"}});
  expect((screen.getByRole("textbox", {name:/Headline manual/}) as HTMLTextAreaElement).value).toBe("");
  fireEvent.change(screen.getByRole("textbox", {name:/Headline manual/}), {target:{value:"Headline do segundo corte"}});
  vi.mocked(api.startRender).mockClear();
  fireEvent.click(screen.getByRole("button", {name:"Renderizar 2 cortes"}));
  await settle();
  expect(vi.mocked(api.startRender).mock.calls.map(call => call[2].headline.text)).toEqual(["Headline do primeiro corte", "Headline do segundo corte"]);
  view.unmount();
  render(<App />);
  await resumeLastEpisode();
  fireEvent.click(screen.getByRole("button", {name:/Editar 2 selecionados/}));
  expect((screen.getByRole("combobox", {name:"Corte em revisão"}) as HTMLSelectElement).value).toBe("2:60:100:balanced");
  expect((screen.getByRole("textbox", {name:/Headline manual/}) as HTMLTextAreaElement).value).toBe("Headline do segundo corte");
  fireEvent.click(screen.getByRole("button", {name:"Usar sugestão da IA"}));
  fireEvent.click(screen.getByRole("button", {name:"Gerar prévia curta (~10 s)"}));
  await settle();
  expect(vi.mocked(api.startRender).mock.lastCall?.[2].headline.text).toBe("Outra sugestão");
  expect(JSON.parse(localStorage.getItem("cortex-draft:project:run")!).headlines).toEqual({"1:0:40:balanced":"Headline do primeiro corte"});
});

test("opening B from the library clears A's settings, cameras, headlines and exports while returning to A restores its draft", async () => {
  const other: WorkflowRun = {...ready, id:"other-run", project_id:"other-project", message:"Episódio B"};
  const entry = (saved: WorkflowRun) => ({id:saved.id, url:null, title:saved.message, project_ids:[saved.project_id], participant_count:null,
    primary_subject:"Dayon", subject_reference:{}, diarizations:[], source:null, source_bytes:0, jobs:[], renders:[], runs:[saved]});
  vi.mocked(api.episodes).mockResolvedValue([entry(visualRun), entry(other)]);
  await openExport({...visualRun, interviewer_identity_id:"host"});
  fireEvent.change(screen.getByRole("slider", {name:/Tamanho/}), {target:{value:"72"}});
  fireEvent.change(screen.getByRole("textbox", {name:/Headline manual/}), {target:{value:"Somente no episódio A"}});
  fireEvent.click(screen.getByText(/Ajustar enquadramento por câmera/));
  fireEvent.change(screen.getByRole("combobox", {name:"Enquadramento do plano 1"}), {target:{value:"right"}});
  fireEvent.click(screen.getByRole("button", {name:"Renderizar 1 corte"}));
  await settle();
  fireEvent.click(screen.getByRole("button", {name:/Biblioteca/}));
  await settle();
  vi.mocked(api.workflowRun).mockResolvedValueOnce(other);
  fireEvent.click(screen.getByRole("button", {name:/Abrir cortes.*Episódio B/}));
  await settle();
  fireEvent.click(screen.getByRole("button", {name:/Editar 1 selecionado/}));
  expect((screen.getByRole("slider", {name:/Tamanho/}) as HTMLInputElement).value).toBe("56");
  expect((screen.getByRole("textbox", {name:/Headline manual/}) as HTMLTextAreaElement).value).toBe("");
  expect((screen.getByRole("combobox", {name:"Composição"}) as HTMLSelectElement).value).toBe("blurred_background");
  expect(screen.queryByRole("button", {name:"Selecionar entrevistador host"})).toBeNull();
  expect(screen.queryByRole("link", {name:"Baixar MP4"})).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"Gerar prévia curta (~10 s)"}));
  await settle();
  expect(vi.mocked(api.startRender).mock.lastCall).toEqual(["other-project", "edl", expect.objectContaining({headline:expect.objectContaining({text:"Headline real"})}), undefined]);
  expect(vi.mocked(api.startRender).mock.lastCall?.[2].framing.scene_overrides).toBeUndefined();
  fireEvent.click(screen.getByRole("button", {name:/Biblioteca/}));
  await settle();
  fireEvent.click(screen.getByRole("button", {name:/Abrir cortes.*Pronto/}));
  await settle();
  fireEvent.click(screen.getByRole("button", {name:/Editar 1 selecionado/}));
  expect((screen.getByRole("slider", {name:/Tamanho/}) as HTMLInputElement).value).toBe("72");
  expect((screen.getByRole("textbox", {name:/Headline manual/}) as HTMLTextAreaElement).value).toBe("Somente no episódio A");
  expect(screen.getByRole("link", {name:"Baixar MP4"})).toBeTruthy();
  fireEvent.click(screen.getByText(/Ajustar enquadramento por câmera/));
  expect((screen.getByRole("combobox", {name:"Enquadramento do plano 1"}) as HTMLSelectElement).value).toBe("right");
});

test.each(['{invalid', JSON.stringify({schema_version:1, settings:{captions:null}, captionEdits:[], headlines:42}), JSON.stringify({schema_version:99})])("an invalid local draft restores safe defaults: %s", async (draft) => {
  localStorage.setItem("cortex-draft:project:run", draft);
  await openExport(ready, false);
  expect((screen.getByRole("slider", {name:/Tamanho/}) as HTMLInputElement).value).toBe("56");
  expect((screen.getByRole("textbox", {name:/Headline manual/}) as HTMLTextAreaElement).value).toBe("");
  fireEvent.click(screen.getByRole("button", {name:"Gerar prévia curta (~10 s)"}));
  await settle();
  expect(vi.mocked(api.startRender).mock.lastCall?.[2].headline.text).toBe("Headline real");
});

test("library is separate from the three-stage workflow and new episode is the entry screen", async () => {
  render(<App/>); await settle();
  expect(screen.getByRole('heading', {name:'Do episódio aos cortes, em um clique.'})).toBeTruthy();
  const navigation = screen.getByRole('navigation', {name:'Jornada do episódio'});
  expect(navigation.querySelectorAll('button')).toHaveLength(3);
  expect(navigation.textContent).toBe('1Novo episódio2Escolher cortes3Exportar');
  fireEvent.click(screen.getByRole('button', {name:'Biblioteca'})); await settle();
  expect(screen.getByRole('heading', {name:'Seus episódios. Seus cortes.'})).toBeTruthy();
});

test("foreground position and accented manual headline reach both live preview and render", async () => {
  await openExport(ready, false);
  expect(screen.queryByRole('button', {name:'Começar outro episódio'})).toBeNull();
  fireEvent.change(screen.getByRole('slider', {name:/Posição vertical do vídeo/}), {target:{value:'25'}});
  fireEvent.change(screen.getByRole('textbox', {name:/Headline manual/}), {target:{value:'Religiões, consciência e ação'}});
  await settle();
  expect(document.querySelector('[data-cortex-headline-text]')?.textContent).toBe('Religiões, consciência e ação');
  expect((screen.getByAltText('Frame do corte selecionado') as HTMLImageElement).style.objectPosition).toBe('center 25%');
  const headline = screen.getByRole('textbox', {name:/Headline manual/});
  expect(headline.compareDocumentPosition(screen.getByRole('button', {name:'Renderizar 1 corte'})) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(screen.getByRole('button', {name:'Renderizar 1 corte'})); await settle();
  expect(api.startRender).toHaveBeenCalledWith('project','edl',expect.objectContaining({framing:expect.objectContaining({position_y:.25}),headline:expect.objectContaining({text:'Religiões, consciência e ação'})}),undefined);
});

test("export details show server frame progress and camera messages stay in export", async () => {
  const pending = deferred<ApiJob>();
  vi.mocked(api.watchJob).mockImplementation(async (id,onUpdate) => {
    if (id === 'render') {onUpdate?.({id,stage:'render',status:'running',progress:18,message:'Remotion: 42/300 quadros renderizados; 20/300 codificados',updated_at:'2026-09-11T08:00:00Z',worker_pid:456}); return pending.promise;}
    return job(id, {edit_plan_artifact_id:'edl'});
  });
  await openExport(ready,false);
  fireEvent.click(screen.getByRole('button', {name:'Renderizar 1 corte'})); await settle();
  fireEvent.click(screen.getByText('Exibir detalhes do processamento'));
  const details = document.querySelector('.job-progress details')!;
  expect(details.textContent).toContain('42/300 quadros');
  expect(details.textContent).toContain('456');
  await act(async () => pending.resolve(job('render',{render_artifact_id:'mp4'})));
  fireEvent.click(screen.getByRole('button', {name:/2Escolher cortes/}));
  expect(screen.queryByRole('status',{name:'Avisos de câmera'})).toBeNull();
  expect(screen.queryByRole('region',{name:'Progresso da exportação'})).toBeNull();
});

test("single-layout identity message distinguishes detected faces from confirmed people", async () => {
  vi.mocked(api.identityIndex).mockResolvedValue({document:{identities:[{identity_id:'candidate',status:'single_layout',layout_ids:['wide']} ]}} as Awaited<ReturnType<typeof api.identityIndex>>);
  await openExport(visualRun);
  fireEvent.click(screen.getByText('Reações e câmeras · opcional'));
  expect(screen.getByText(/Foram encontrados rostos, mas os trechos analisados têm apenas um ângulo/)).toBeTruthy();
  expect(screen.queryByRole('button', {name:/Selecionar entrevistador/})).toBeNull();
});

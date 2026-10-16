import { useCallback, useEffect, useRef, useState } from "react";
import { api, isRemoteOrigin, REMOTE_UPLOAD_LIMIT_BYTES, REMOTE_UPLOAD_MESSAGE, type ApiJob, type EpisodeEntry } from "../../api";
import { errorText } from "../../app/humanize";
import { patchTask, upsertTask } from "../../app/tasks";
import { episodePath, navigate } from "../../router";
import { briefOf, type RunOptions } from "./runOptions";

export type RunSource =
  | { kind: "youtube"; url: string }
  | { kind: "file"; file: File }
  | { kind: "episode"; entry: EpisodeEntry };

export type CreateState = { busy: boolean; message: string; progress: number };

async function downloadSource(episodeId: string, job: ApiJob, taskId: string) {
  const done = await api.watchJob(job.id, update => patchTask(taskId, { message: `Baixando o vídeo ${Math.round(update.progress || 0)}%`, progress: update.progress || 0 }));
  if (done.status !== "succeeded") throw new Error(done.error || done.message || "O download não terminou.");
  const asset = done.result?.source_asset_id;
  if (typeof asset !== "string" || !asset) throw new Error("O download terminou sem o arquivo do vídeo.");
  return asset;
}

/** Download (if needed) and run creation, detached from any page so navigation does not interrupt it. */
async function continueInBackground(taskId: string, entry: EpisodeEntry | null, ready: { projectId: string; sourceAssetId: string } | null, options: RunOptions) {
  try {
    let target = ready;
    if (!target) {
      const download = await api.downloadEpisode(entry!.id);
      if (download.source) target = { projectId: download.source.project_id, sourceAssetId: download.source.id };
      else if (download.job) target = { projectId: entry!.project_ids[0], sourceAssetId: await downloadSource(entry!.id, download.job, taskId) };
      else throw new Error("O download não devolveu o vídeo nem uma tarefa.");
    }
    patchTask(taskId, { message: "Criando a seleção de cortes…", progress: 100 });
    await api.createWorkflowRun(target.projectId, target.sourceAssetId, briefOf(options));
    patchTask(taskId, { status: "done", message: "Seleção criada." });
  } catch (reason) {
    patchTask(taskId, { status: "failed", error: errorText(reason) });
  }
}

/**
 * Registers or uploads the episode, opens its page right away and lets the rest continue as a task.
 * Resolves with an error message when something failed before the page could open.
 */
export function useCreateEpisodeRun() {
  const [state, setState] = useState<CreateState>({ busy: false, message: "", progress: 0 });
  const busy = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const start = useCallback(async (source: RunSource, options: RunOptions): Promise<string | null> => {
    if (busy.current) return null;
    busy.current = true;
    const report = (message: string, progress = 0) => { if (mounted.current) setState({ busy: true, message, progress }); };
    try {
      if (source.kind === "youtube" && !/^https?:\/\//.test(source.url.trim())) throw new Error("Cole um link válido do YouTube.");
      if (source.kind === "file" && isRemoteOrigin(window.location.hostname) && source.file.size > REMOTE_UPLOAD_LIMIT_BYTES) throw new Error(REMOTE_UPLOAD_MESSAGE);
      report("Preparando o episódio…");
      let episodeId: string;
      let entry: EpisodeEntry | null = null;
      let ready: { projectId: string; sourceAssetId: string } | null = null;
      if (source.kind === "file") {
        const project = await api.createProject(source.file.name.replace(/\.[^.]+$/, "") || "Novo episódio");
        const uploaded = await api.uploadSource(project.id, source.file, fraction => report("Enviando o arquivo…", fraction * 100));
        ready = { projectId: project.id, sourceAssetId: uploaded.id };
        episodeId = `local-${project.id}`;
        await api.episodes();
      } else {
        if (source.kind === "youtube") {
          entry = await api.registerEpisode(source.url.trim());
          if (entry.archived) await api.archiveEpisode(entry.id, false);
          if (!entry.metadata_updated_at) await api.refreshEpisodeMetadata(entry.id);
        } else entry = source.entry;
        episodeId = entry.id;
        if (entry.source) ready = { projectId: entry.source.project_id, sourceAssetId: entry.source.id };
      }
      const taskId = `${episodeId}:${Date.now()}`;
      upsertTask({ id: taskId, episodeId, status: "running", progress: 0, message: ready ? "Criando a seleção de cortes…" : "Baixando o vídeo…" });
      void continueInBackground(taskId, entry, ready, options);
      if (mounted.current) setState({ busy: false, message: "", progress: 0 });
      navigate(episodePath(episodeId));
      return null;
    } catch (reason) {
      if (mounted.current) setState({ busy: false, message: "", progress: 0 });
      return errorText(reason);
    } finally { busy.current = false; }
  }, []);

  return { ...state, start };
}

import { useCallback, useEffect, useRef, useState } from "react";
import { api, isRemoteOrigin, REMOTE_UPLOAD_LIMIT_BYTES, REMOTE_UPLOAD_MESSAGE, type ApiJob, type EpisodeEntry } from "../../api";
import { errorText } from "../../app/humanize";
import { episodePath, navigate } from "../../router";
import { briefOf, type RunOptions } from "./runOptions";

export type RunSource =
  | { kind: "youtube"; url: string }
  | { kind: "file"; file: File }
  | { kind: "episode"; entry: EpisodeEntry };

export type CreateState = { busy: boolean; message: string; progress: number };

async function finishJob(job: ApiJob, signal: AbortSignal, onUpdate: (job: ApiJob) => void) {
  signal.throwIfAborted();
  const done = await api.watchJob(job.id, onUpdate, signal);
  signal.throwIfAborted();
  if (done.status !== "succeeded") throw new Error(done.error || done.message || "O download não terminou.");
  const asset = done.result?.source_asset_id;
  if (typeof asset !== "string" || !asset) throw new Error("O download terminou sem o arquivo do vídeo.");
  return asset;
}

/** Registers or uploads the episode, waits for the source, creates the run and opens the episode page. */
export function useCreateEpisodeRun(onDone?: (episodeId: string) => void) {
  const [state, setState] = useState<CreateState>({ busy: false, message: "", progress: 0 });
  const controller = useRef<AbortController | null>(null);
  const busy = useRef(false);
  useEffect(() => () => controller.current?.abort(), []);

  const start = useCallback(async (source: RunSource, options: RunOptions): Promise<string | null> => {
    if (busy.current) return null;
    const signal = (controller.current = new AbortController()).signal;
    busy.current = true;
    const report = (message: string, progress = 0) => { if (!signal.aborted) setState({ busy: true, message, progress }); };
    try {
      if (source.kind === "youtube" && !/^https?:\/\//.test(source.url.trim())) throw new Error("Cole um link válido do YouTube.");
      if (source.kind === "file" && isRemoteOrigin(window.location.hostname) && source.file.size > REMOTE_UPLOAD_LIMIT_BYTES) throw new Error(REMOTE_UPLOAD_MESSAGE);
      report("Preparando o episódio…");
      let projectId: string;
      let sourceAssetId: string;
      let episodeId: string;
      if (source.kind === "file") {
        const project = await api.createProject(source.file.name.replace(/\.[^.]+$/, "") || "Novo episódio");
        signal.throwIfAborted();
        const uploaded = await api.uploadSource(project.id, source.file, fraction => report("Enviando o arquivo…", fraction * 100));
        signal.throwIfAborted();
        projectId = project.id; sourceAssetId = uploaded.id; episodeId = `local-${project.id}`;
      } else {
        let entry: EpisodeEntry;
        if (source.kind === "youtube") {
          entry = await api.registerEpisode(source.url.trim());
          signal.throwIfAborted();
          if (entry.archived) await api.archiveEpisode(entry.id, false);
          if (!entry.metadata_updated_at) await api.refreshEpisodeMetadata(entry.id);
          signal.throwIfAborted();
        } else entry = source.entry;
        episodeId = entry.id;
        if (entry.source) {
          projectId = entry.source.project_id; sourceAssetId = entry.source.id;
        } else {
          const download = await api.downloadEpisode(entry.id);
          signal.throwIfAborted();
          if (download.source) {
            projectId = download.source.project_id; sourceAssetId = download.source.id;
          } else if (download.job) {
            projectId = entry.project_ids[0];
            report("Baixando o vídeo…");
            sourceAssetId = await finishJob(download.job, signal, job => report("Baixando o vídeo…", job.progress || 0));
          } else throw new Error("O download não devolveu o vídeo nem uma tarefa.");
        }
      }
      report("Criando a seleção de cortes…", 100);
      await api.createWorkflowRun(projectId, sourceAssetId, briefOf(options));
      signal.throwIfAborted();
      if (source.kind === "file") await api.episodes();
      signal.throwIfAborted();
      setState({ busy: false, message: "", progress: 0 });
      if (onDone) onDone(episodeId); else navigate(episodePath(episodeId));
      return null;
    } catch (reason) {
      if (signal.aborted) return null;
      setState({ busy: false, message: "", progress: 0 });
      return errorText(reason);
    } finally { busy.current = false; }
  }, [onDone]);

  return { ...state, start };
}

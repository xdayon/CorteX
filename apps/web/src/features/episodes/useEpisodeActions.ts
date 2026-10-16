import { useCallback, useEffect, useRef, useState } from "react";
import { api, type EpisodeEntry } from "../../api";
import { errorText } from "../../app/humanize";
import { useToast } from "../../ui";

export type EpisodeAction = "download" | "metadata" | "archive";

export function useEpisodeActions(refresh: () => Promise<void>, invalidate: () => void) {
  const toast = useToast();
  const [pending, setPending] = useState<Record<string, EpisodeAction>>({});
  const [voiceFor, setVoiceFor] = useState<EpisodeEntry | null>(null);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  const act = useCallback(async (entry: EpisodeEntry, action: EpisodeAction) => {
    invalidate();
    setPending(current => ({ ...current, [entry.id]: action }));
    try {
      if (action === "download") {
        await api.downloadEpisode(entry.id);
        toast("Download iniciado. O andamento aparece no topo da página.", "ok");
      }
      if (action === "metadata") {
        await api.refreshEpisodeMetadata(entry.id);
        toast("Dados do YouTube atualizados.", "ok");
      }
      if (action === "archive") {
        await api.archiveEpisode(entry.id, !entry.archived);
        toast(entry.archived ? "Episódio desarquivado." : "Episódio arquivado.", "ok");
      }
      if (mounted.current) await refresh();
    } catch (reason) {
      toast(errorText(reason), "bad");
    } finally {
      if (mounted.current) setPending(current => { const next = { ...current }; delete next[entry.id]; return next; });
    }
  }, [invalidate, refresh, toast]);

  return { pending, act, voiceFor, setVoiceFor };
}

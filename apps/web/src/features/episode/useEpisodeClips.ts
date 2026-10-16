import { useCallback, useEffect, useState } from "react";
import { api, type ClipRow, type EpisodeClips } from "../../api";
import { errorText } from "../../app/humanize";

/** Loads the integrated clip view of an episode; `refreshKey` changes force a reload. */
export function useEpisodeClips(episodeId: string, refreshKey: string) {
  const [data, setData] = useState<EpisodeClips | null>(null);
  const [rows, setRows] = useState<ClipRow[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    api.episodeClips(episodeId, controller.signal).then(result => {
      if (controller.signal.aborted || !result) return;
      setData(result); setRows(result.clips); setError(""); setLoading(false);
    }).catch(reason => {
      if (controller.signal.aborted) return;
      setError(errorText(reason)); setLoading(false);
    });
    return () => controller.abort();
  }, [episodeId, refreshKey, version]);

  const reload = useCallback(() => setVersion(value => value + 1), []);
  return { data, rows, setRows, loading, error, reload };
}

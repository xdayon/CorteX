import { useCallback, useEffect, useRef, useState } from "react";
import { api, type EpisodeEntry } from "../../api";
import { activeJobOf, activeRunOf, errorText } from "../../app/humanize";

/** Loads the episode catalogue, refreshes on focus and polls while any episode is processing. */
export function useEpisodeCatalog(includeArchived: boolean, pollMs = 5000) {
  const [entries, setEntries] = useState<EpisodeEntry[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const mounted = useRef(false);
  const fetching = useRef(false);
  const filter = useRef(includeArchived);

  const refresh = useCallback(async () => {
    const request = ++generation.current;
    fetching.current = true;
    try {
      const result = await api.episodes(filter.current);
      if (mounted.current && request === generation.current) { setEntries(Array.isArray(result) ? result : []); setError(""); }
    } catch (reason) {
      if (mounted.current && request === generation.current) setError(errorText(reason));
    } finally {
      if (mounted.current && request === generation.current) { fetching.current = false; setLoading(false); }
    }
  }, []);

  useEffect(() => {
    mounted.current = true; filter.current = includeArchived; setLoading(true);
    void refresh();
    function resume() { if (!document.hidden && !fetching.current) void refresh(); }
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      mounted.current = false; generation.current++;
      window.removeEventListener("focus", resume); document.removeEventListener("visibilitychange", resume);
    };
  }, [includeArchived, refresh]);

  const processing = entries.some(entry => !!activeJobOf(entry) || !!activeRunOf(entry));
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => { if (!document.hidden && !fetching.current) void refresh(); }, pollMs);
    return () => window.clearInterval(timer);
  }, [processing, pollMs, refresh]);

  /** Drops any in-flight response so an optimistic action is not overwritten by stale data. */
  const invalidate = useCallback(() => { generation.current++; fetching.current = false; }, []);
  return { entries, loading, error, refresh, invalidate };
}

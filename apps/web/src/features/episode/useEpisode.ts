import { useCallback, useEffect, useRef, useState } from "react";
import { api, type EpisodeEntry } from "../../api";
import { activeJobOf, activeRunOf, errorText } from "../../app/humanize";

/** Loads one episode, refreshes on focus and polls only while it is processing. */
export function useEpisode(id: string, pollMs = 3000) {
  const [entry, setEntry] = useState<EpisodeEntry | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const generation = useRef(0);
  const mounted = useRef(false);
  const fetching = useRef(false);

  const refresh = useCallback(async () => {
    const request = ++generation.current;
    fetching.current = true;
    try {
      const result = await api.episode(id);
      if (mounted.current && request === generation.current) { setEntry(result ?? null); setError(""); }
    } catch (reason) {
      if (mounted.current && request === generation.current) setError(errorText(reason));
    } finally {
      if (mounted.current && request === generation.current) { fetching.current = false; setLoading(false); }
    }
  }, [id]);

  useEffect(() => {
    mounted.current = true; setLoading(true);
    void refresh();
    function resume() { if (!document.hidden && !fetching.current) void refresh(); }
    window.addEventListener("focus", resume);
    document.addEventListener("visibilitychange", resume);
    return () => {
      mounted.current = false; generation.current++;
      window.removeEventListener("focus", resume); document.removeEventListener("visibilitychange", resume);
    };
  }, [refresh]);

  const processing = !!entry && (!!activeJobOf(entry) || !!activeRunOf(entry));
  useEffect(() => {
    if (!processing) return;
    const timer = window.setInterval(() => { if (!document.hidden && !fetching.current) void refresh(); }, pollMs);
    return () => window.clearInterval(timer);
  }, [processing, pollMs, refresh]);

  /** Drops any in-flight response so an optimistic action is not overwritten by stale data. */
  const invalidate = useCallback(() => { generation.current++; fetching.current = false; }, []);
  return { entry, loading, error, refresh, invalidate };
}

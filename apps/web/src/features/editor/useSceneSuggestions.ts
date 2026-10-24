import { useCallback, useEffect, useState } from "react";
import { api, type SceneSuggestions } from "../../api";
import { errorText } from "../../app/humanize";

export type SceneData = {
  data: SceneSuggestions | null; loading: boolean; error: string; refresh: () => void;
  /** Patches the cached answer locally (decision changes) without another request. */
  patch: (change: (data: SceneSuggestions) => SceneSuggestions) => void;
};

/** Scene suggestions of the clip's current edit plan; refetched when the plan changes and on `refresh()`. */
export function useSceneSuggestions(episodeId: string, planId: string | null): SceneData {
  const [data, setData] = useState<SceneSuggestions | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [round, setRound] = useState(0);

  useEffect(() => {
    setData(null); setError("");
    if (!planId) { setLoading(false); return; }
    const controller = new AbortController();
    setLoading(true);
    void Promise.resolve(api.scenes(episodeId, planId, controller.signal))
      .then(result => { if (!controller.signal.aborted) { setData(result ?? null); setLoading(false); } })
      .catch(reason => { if (!controller.signal.aborted) { setError(errorText(reason)); setLoading(false); } });
    return () => controller.abort();
  }, [episodeId, planId, round]);

  const refresh = useCallback(() => setRound(value => value + 1), []);
  const patch = useCallback((change: (data: SceneSuggestions) => SceneSuggestions) => setData(current => (current ? change(current) : current)), []);
  return { data, loading, error, refresh, patch };
}

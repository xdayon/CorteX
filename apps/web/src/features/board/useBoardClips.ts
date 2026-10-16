import { useEffect, useState } from "react";
import { api, type BoardClip } from "../../api";
import { errorText } from "../../app/humanize";

export function useBoardClips() {
  const [rows, setRows] = useState<BoardClip[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    const controller = new AbortController();
    api.clips([], controller.signal).then(result => {
      if (controller.signal.aborted) return;
      setRows(result?.clips ?? []); setLoading(false);
    }).catch(reason => { if (!controller.signal.aborted) { setError(errorText(reason)); setLoading(false); } });
    return () => controller.abort();
  }, []);
  return { rows, setRows, loading, error };
}

import { useCallback, useEffect, useState } from "react";
import { api, type DriveState, type ExportBatch } from "../../api";
import { errorText } from "../../app/humanize";

const WAITING = ["waiting", "uploading"];

/** Google Drive delivery of every exported clip of the batches, refreshed while an upload is still running. */
export function useDriveStates(batches: ExportBatch[]) {
  const [states, setStates] = useState<Record<string, DriveState>>({});
  const [round, setRound] = useState(0);
  const [retryError, setRetryError] = useState("");
  const exported = batches.map(batch => batch.items.filter(item => item.status === "succeeded").map(item => item.id).join(",")).join("|");

  useEffect(() => {
    const active = batches.filter(batch => batch.items.some(item => item.status === "succeeded"));
    if (!active.length) return;
    const controller = new AbortController();
    let timer = 0;
    void Promise.all(active.map(batch => api.driveStates(batch.project_id, batch.id, controller.signal)))
      .then(all => {
        const merged = Object.assign({}, ...all) as Record<string, DriveState>;
        setStates(current => ({ ...current, ...merged }));
        const pending = active.some(batch => batch.items.some(item => item.status === "succeeded" && WAITING.includes(merged[item.id]?.state ?? "uploading")));
        if (pending) timer = window.setTimeout(() => setRound(value => value + 1), 2000);
      }).catch(() => undefined);
    return () => { controller.abort(); window.clearTimeout(timer); };
    // The batches list is replaced on every poll; only the exported items and the round matter.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [exported, round]);

  const retry = useCallback(async () => {
    setRetryError("");
    try {
      const all = await Promise.all(batches.map(batch => api.retryDrive(batch.project_id, batch.id)));
      setStates(current => ({ ...current, ...Object.assign({}, ...all) }));
      setRound(value => value + 1);
    } catch (reason) { setRetryError(errorText(reason)); }
  }, [batches]);

  return { states, retry, retryError };
}

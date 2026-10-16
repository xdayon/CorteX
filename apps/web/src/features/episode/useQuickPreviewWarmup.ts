import { useEffect } from "react";
import { api, type ClipRow } from "../../api";
import { rowId } from "../clips/clipModel";

export const WARMUP_AHEAD = 5;

/** Renders quick previews one at a time: the active clip first, then the next few without an export. */
export function useQuickPreviewWarmup(visible: ClipRow[], activeId: string) {
  useEffect(() => {
    const start = visible.findIndex(row => rowId(row) === activeId);
    if (start < 0) return;
    const queue = [visible[start], ...visible.slice(start + 1).filter(row => !row.exports.length).slice(0, WARMUP_AHEAD)];
    const controller = new AbortController();
    void (async () => {
      for (const row of queue) {
        if (controller.signal.aborted) return;
        try {
          await api.warmQuickPreview(api.quickPreviewUrl(row.project_id, row.run_id, row.clip_key), controller.signal);
        } catch { if (controller.signal.aborted) return; }
      }
    })();
    return () => controller.abort();
  }, [visible, activeId]);
}

import { useCallback, useEffect, useState } from "react";
import { api, type ClipRevision, type ExportBatch, type WorkflowRun } from "../../api";
import { errorText } from "../../app/humanize";

const FINISHED = ["succeeded", "failed", "cancelled"];
export const isOpen = (batch: ExportBatch) => ["queued", "running"].includes(batch.status);

/** Share of finished items, over every batch of the export (one batch per run). */
export function batchProgress(batches: ExportBatch[]) {
  const items = batches.flatMap(batch => batch.items);
  return (items.filter(item => FINISHED.includes(item.status)).length / Math.max(1, items.length)) * 100;
}

/** Runs one durable export batch per run, tracking them together; the revisions come from the editor's own save. */
export function useExportBatches({ runs, runIds, ready, initial, flushSave, signal }: {
  runs: Record<string, WorkflowRun>; runIds: string[]; ready: boolean; initial: Record<string, ExportBatch>;
  flushSave: () => Promise<Record<string, ClipRevision>>; signal: () => AbortSignal | undefined;
}) {
  const [batches, setBatches] = useState<Record<string, ExportBatch>>({});
  const [busy, setBusy] = useState(false);
  const [exportProgress, setExportProgress] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [exportError, setExportError] = useState("");

  useEffect(() => {
    setBatches(initial);
    setExportProgress(batchProgress(Object.values(initial)));
    setWarnings([]); setExportError("");
  }, [initial]);

  useEffect(() => {
    const open = Object.entries(batches).filter(([, batch]) => isOpen(batch));
    if (!ready || !open.length) return;
    const timer = window.setTimeout(() => {
      void Promise.all(open.map(([runId, batch]) => api.exportBatch(batch.project_id, batch.id).then(next => [runId, next] as const))).then(updated => {
        setBatches(current => ({ ...current, ...Object.fromEntries(updated) }));
        setExportProgress(batchProgress(updated.map(([, batch]) => batch)));
      }).catch(reason => setWarnings(items => [...items, `Não foi possível atualizar o lote: ${errorText(reason)}`]));
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [ready, batches]);

  const [finalizing, setFinalizing] = useState<string[] | null>(null);
  const exportSelected = useCallback(async () => {
    const lifetime = signal();
    if (!ready || busy || !runIds.length || !lifetime) return;
    setBusy(true); setExportError(""); setExportProgress(0);
    try {
      const revisions = await flushSave();
      if (lifetime.aborted) return;
      const created: Record<string, ExportBatch> = {};
      for (const runId of runIds) {
        const revision = revisions[runId];
        if (!revision) throw new Error("Não foi possível salvar a revisão antes de exportar.");
        created[runId] = await api.createExportBatch(runs[runId].project_id, runId, revision.id, `${runId}:${crypto.randomUUID()}`);
        setBatches(previous => ({ ...previous, [runId]: created[runId] }));
      }
      setFinalizing(runIds);
    } catch (reason) {
      if (!lifetime.aborted) { setExportError(errorText(reason)); setBusy(false); }
    }
  }, [ready, busy, runIds, runs, flushSave, signal]);

  useEffect(() => {
    const own = finalizing;
    if (!busy || !own) return;
    const mine = own.flatMap(runId => batches[runId] ? [batches[runId]] : []);
    if (mine.length < own.length || mine.some(isOpen)) return;
    setFinalizing(null);
    const items = mine.flatMap(batch => batch.items);
    const failures = items.filter(item => item.status === "failed");
    if (failures.length) setWarnings(list => [...list, ...failures.map(item => `“${item.title}”: ${item.error || "falha na exportação"}`)]);
    if (!items.some(item => item.status === "succeeded" && item.render_artifact_id)) {
      setExportError(mine.find(batch => batch.error)?.error || "Nenhum corte foi exportado.");
    } else setExportProgress(100);
    setBusy(false);
  }, [busy, batches, finalizing]);

  const patchBatches = useCallback((eligible: (batch: ExportBatch) => boolean, action: (projectId: string, batchId: string) => Promise<ExportBatch>) => {
    for (const [runId, batch] of Object.entries(batches)) {
      if (!eligible(batch)) continue;
      void action(batch.project_id, batch.id).then(next => setBatches(current => ({ ...current, [runId]: next }))).catch(reason => setExportError(errorText(reason)));
    }
  }, [batches]);

  return {
    batches, busy, exportProgress, warnings, exportError, setExportError, exportSelected,
    cancelBatches: () => patchBatches(isOpen, api.cancelExportBatch),
    retryBatches: () => patchBatches(batch => batch.items.some(item => item.status === "failed"), api.retryExportBatch),
  };
}

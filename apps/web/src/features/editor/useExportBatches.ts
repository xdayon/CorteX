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

function waitForPoll(signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const timer = window.setTimeout(() => { signal.removeEventListener("abort", abort); resolve(); }, 1200);
    function abort() { window.clearTimeout(timer); reject(signal.reason); }
    signal.addEventListener("abort", abort, { once: true });
  });
}

type Revision = Parameters<typeof api.saveRevision>[2];

/** Saves one revision per run and runs one durable export batch per run, tracking them together. */
export function useExportBatches({ runs, runIds, ready, initial, revisionBody, signal }: {
  runs: Record<string, WorkflowRun>; runIds: string[]; ready: boolean; initial: Record<string, ExportBatch>;
  revisionBody: (runId: string) => Revision; signal: () => AbortSignal | undefined;
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
    if (busy || !ready || !open.length) return;
    const timer = window.setTimeout(() => {
      void Promise.all(open.map(([runId, batch]) => api.exportBatch(batch.project_id, batch.id).then(next => [runId, next] as const))).then(updated => {
        setBatches(current => ({ ...current, ...Object.fromEntries(updated) }));
        setExportProgress(batchProgress(updated.map(([, batch]) => batch)));
      }).catch(reason => setWarnings(items => [...items, `Não foi possível atualizar o lote: ${errorText(reason)}`]));
    }, 1200);
    return () => window.clearTimeout(timer);
  }, [busy, ready, batches]);

  const exportSelected = useCallback(async () => {
    const lifetime = signal();
    if (!ready || busy || !runIds.length || !lifetime) return;
    setBusy(true); setExportError(""); setExportProgress(0);
    try {
      const current: Record<string, ExportBatch> = {};
      for (const runId of runIds) {
        const run = runs[runId];
        const revision: ClipRevision | undefined = await api.saveRevision(run.project_id, runId, revisionBody(runId));
        if (!revision) throw new Error("Não foi possível salvar a revisão antes de exportar.");
        current[runId] = await api.createExportBatch(run.project_id, runId, revision.id, `${runId}:${crypto.randomUUID()}`);
        setBatches(previous => ({ ...previous, [runId]: current[runId] }));
      }
      while (Object.values(current).some(isOpen)) {
        setExportProgress(batchProgress(Object.values(current)));
        await waitForPoll(lifetime);
        for (const [runId, batch] of Object.entries(current)) {
          if (!isOpen(batch)) continue;
          current[runId] = await api.exportBatch(batch.project_id, batch.id, lifetime);
        }
        setBatches(previous => ({ ...previous, ...current }));
      }
      const items = Object.values(current).flatMap(batch => batch.items);
      const failures = items.filter(item => item.status === "failed");
      if (failures.length) setWarnings(list => [...list, ...failures.map(item => `“${item.title}”: ${item.error || "falha na exportação"}`)]);
      if (!items.some(item => item.status === "succeeded" && item.render_artifact_id)) {
        throw new Error(Object.values(current).find(batch => batch.error)?.error || "Nenhum corte foi exportado.");
      }
      setExportProgress(100);
    } catch (reason) {
      if (!lifetime.aborted) setExportError(errorText(reason));
    } finally { if (!lifetime.aborted) setBusy(false); }
  }, [ready, busy, runIds, runs, revisionBody, signal]);

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

import { useCallback } from "react";
import { api, type ClipDecision, type ClipRow, type DiscardReason } from "../../api";
import { errorText } from "../../app/humanize";
import { useToast } from "../../ui";
import { deriveStatus, rowId, withState } from "./clipModel";

export type ClipChange = { decision?: ClipDecision; discard_reason?: DiscardReason | null; saved?: boolean };
type SetRows<R> = (update: (rows: R[]) => R[]) => void;

/** Optimistic clip state changes: the UI updates first, a failed PATCH rolls back and shows a toast. */
export function useClipMutations<R extends ClipRow>(setRows: SetRows<R>) {
  const toast = useToast();
  return useCallback(async (targets: R[], change: ClipChange) => {
    if (!targets.length) return;
    const before = new Map(targets.map(row => [rowId(row), row.state]));
    const local = { ...change, ...(change.saved === undefined ? {} : { saved_at: change.saved ? new Date().toISOString() : null }) };
    setRows(rows => rows.map(row => before.has(rowId(row)) ? withState(row, local) : row));
    const body = { ...change };
    if (change.decision !== undefined && change.decision !== "discarded") delete body.discard_reason;
    try {
      await api.patchClipStates(targets.map(row => ({ project_id: row.project_id, run_id: row.run_id, clip_key: row.clip_key, ...body })));
    } catch (reason) {
      setRows(rows => rows.map(row => {
        if (!before.has(rowId(row))) return row;
        const state = before.get(rowId(row)) ?? null;
        return { ...row, state, status: deriveStatus(state, row.exports, row.publication) };
      }));
      toast(`Não foi possível salvar a mudança: ${errorText(reason)}`, "bad");
    }
  }, [setRows, toast]);
}

import { useMemo } from "react";
import type { ClipRow, DiscardReason } from "../../api";
import { editorPath, navigate } from "../../router";
import { useToast } from "../../ui";
import { downloadAll, headlineOf, newestExport, rowId } from "./clipModel";
import { useClipMutations } from "./useClipMutations";

type SetRows<R> = (update: (rows: R[]) => R[]) => void;

/** The actions shared by the episode page and the board, applied to one or many clips. */
export function useClipCommands<R extends ClipRow>(setRows: SetRows<R>, episodeIdOf: (row: R) => string) {
  const toast = useToast();
  const mutate = useClipMutations<R>(setRows);

  return useMemo(() => ({
    approve: (rows: R[]) => mutate(rows, { decision: rows.every(row => row.state?.decision === "approved") ? null : "approved" }),
    discard: (rows: R[], reason: DiscardReason) => mutate(rows, { decision: "discarded", discard_reason: reason }),
    toggleSaved: (rows: R[]) => {
      const exported = rows.filter(row => row.exports.length);
      if (!exported.length) return;
      return mutate(exported, { saved: !exported.every(row => row.state?.saved) });
    },
    togglePosted: (rows: R[]) => {
      const exported = rows.filter(row => row.exports.length);
      if (!exported.length) return;
      return mutate(exported, { posted: !exported.every(row => row.state?.posted) });
    },
    download: (rows: R[]) => {
      const items = rows.flatMap(row => { const item = newestExport(row); return item ? [{ title: headlineOf(row), url: item.media_url }] : []; });
      return downloadAll(items);
    },
    edit: (rows: R[]) => {
      if (!rows.length) return;
      if (new Set(rows.map(episodeIdOf)).size > 1) { toast("Escolha cortes de um mesmo episódio para editar juntos."); return; }
      navigate(editorPath(episodeIdOf(rows[0]), rows.map(rowId)));
    },
  }), [mutate, episodeIdOf, toast]);
}

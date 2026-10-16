import { forwardRef, useEffect, useMemo, useState } from "react";
import { api, type ClipRow } from "../../api";
import { errorText } from "../../app/humanize";
import { Button, Skeleton } from "../../ui";
import { SourcePiecesPlayer, type PlayerHandle } from "./SourcePiecesPlayer";

export function piecesOf(row: ClipRow) {
  const pieces = (row.clip.pieces ?? []).map(piece => ({ start: piece.start, end: piece.end }));
  return pieces.length ? pieces : [{ start: row.start_second, end: row.end_second }];
}

type Phase = "loading" | "ready" | "error";

/** Vertical 9:16 H.264 preview of an un-exported clip, rendered and cached by the server. */
export const QuickPreviewPlayer = forwardRef<PlayerHandle, { row: ClipRow }>(function QuickPreviewPlayer({ row }, handle) {
  const url = api.quickPreviewUrl(row.project_id, row.run_id, row.clip_key);
  const [phase, setPhase] = useState<Phase>("loading");
  const [message, setMessage] = useState("");
  const [attempt, setAttempt] = useState(0);
  // The preview is one concatenated file: pieces become contiguous spans so junction markers keep their durations.
  const pieces = useMemo(() => {
    let cursor = 0;
    return piecesOf(row).map(piece => {
      const span = { start: cursor, end: cursor + (piece.end - piece.start) };
      cursor = span.end;
      return span;
    });
  }, [row]);

  useEffect(() => {
    const controller = new AbortController();
    setPhase("loading");
    api.warmQuickPreview(url, controller.signal).then(() => {
      if (!controller.signal.aborted) setPhase("ready");
    }).catch(reason => {
      if (controller.signal.aborted) return;
      setMessage(errorText(reason)); setPhase("error");
    });
    return () => controller.abort();
  }, [url, attempt]);

  return <div className="qp">
    {phase === "ready" && <SourcePiecesPlayer ref={handle} src={url} pieces={pieces} portrait label="Prévia rápida vertical" />}
    {phase === "loading" && <div className="qp-state" role="status" aria-label="Gerando prévia vertical">
      <Skeleton width="100%" height="100%" style={{ position: "absolute", inset: 0, borderRadius: 12 }} />
      <span className="qp-msg">Gerando prévia vertical…</span>
    </div>}
    {phase === "error" && <div className="qp-state" role="alert">
      <div className="qp-msg"><p>Não foi possível gerar a prévia: {message}</p>
        <Button size="sm" onClick={() => setAttempt(value => value + 1)}>Tentar de novo</Button></div>
    </div>}
    <p className="qp-label">Prévia rápida · legenda e enquadramento por rosto no editor</p>
  </div>;
});

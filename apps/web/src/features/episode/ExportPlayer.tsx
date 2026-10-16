import { forwardRef, useMemo, useState } from "react";
import { api, type ClipExport } from "../../api";
import { SourcePiecesPlayer, type PlayerHandle } from "./SourcePiecesPlayer";

/** The exported MP4 behind the same custom transport as the source pieces player. */
export const ExportPlayer = forwardRef<PlayerHandle, { projectId: string; item: ClipExport }>(function ExportPlayer({ projectId, item }, handle) {
  const [measured, setMeasured] = useState(0);
  const end = measured || item.duration_seconds || 0.1;
  const pieces = useMemo(() => [{ start: 0, end }], [end]);
  return <SourcePiecesPlayer ref={handle} src={item.media_url} pieces={pieces} portrait label="Vídeo exportado"
    poster={api.posterUrl(projectId, item.render_artifact_id)} onMetadata={setMeasured} />;
});

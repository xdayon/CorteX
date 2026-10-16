import { forwardRef, useImperativeHandle, useRef } from "react";
import { api, type ClipExport } from "../../api";
import type { PlayerHandle } from "./SourcePiecesPlayer";

export const ExportPlayer = forwardRef<PlayerHandle, { projectId: string; item: ClipExport }>(function ExportPlayer({ projectId, item }, handle) {
  const video = useRef<HTMLVideoElement>(null);
  useImperativeHandle(handle, () => ({
    toggle: () => { const element = video.current; if (!element) return; if (element.paused) void element.play(); else element.pause(); },
  }), []);
  return <video ref={video} className="exp-video" controls playsInline preload="metadata" aria-label="Vídeo exportado"
    poster={api.posterUrl(projectId, item.render_artifact_id)} src={item.media_url} />;
});

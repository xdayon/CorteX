import { useEffect, useMemo, useState } from "react";
import { api, type CaptionCorrection, type OverlayPreview, type RenderSettings } from "../api";

const DEBOUNCE_MS = 200;

export function overlaySettings(
  settings: RenderSettings, corrections: CaptionCorrection[], emphasis: number[], headlineText: string,
): RenderSettings {
  return {
    ...settings,
    captions: { ...settings.captions, corrections, emphasis_word_indices: emphasis },
    headline: { ...settings.headline, text: headlineText.slice(0, 120) },
  };
}

export function useOverlayPreview({ projectId, editPlanArtifactId, settings, corrections, emphasis, headlineText }: {
  projectId: string; editPlanArtifactId: string | null; settings: RenderSettings;
  corrections: CaptionCorrection[]; emphasis: number[]; headlineText: string;
}) {
  const [overlay, setOverlay] = useState<OverlayPreview | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const body = useMemo(
    () => overlaySettings(settings, corrections, emphasis, headlineText),
    [settings, corrections, emphasis, headlineText],
  );
  const serialized = JSON.stringify(body);

  useEffect(() => { setOverlay(null); setError(""); }, [editPlanArtifactId]);

  useEffect(() => {
    if (!editPlanArtifactId) return;
    const controller = new AbortController();
    setLoading(true);
    const timer = window.setTimeout(() => {
      void api.overlayPreview(projectId, editPlanArtifactId, body, controller.signal).then(result => {
        if (controller.signal.aborted) return;
        setOverlay(result); setError("");
      }).catch(reason => {
        if (controller.signal.aborted) return;
        setError(reason instanceof Error ? reason.message : String(reason));
      }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    }, DEBOUNCE_MS);
    return () => { window.clearTimeout(timer); controller.abort(); };
    // `serialized` tracks the content; `body` is derived from the same inputs.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, editPlanArtifactId, serialized]);

  return { overlay, error, loading };
}

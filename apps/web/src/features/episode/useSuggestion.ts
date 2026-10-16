import { useEffect, useState } from "react";
import { api, type SuggestionSelection, type WorkflowRun } from "../../api";
import { errorText, isRunReady } from "../../app/humanize";

/** Loads the persisted selection of a ready run. */
export function useSuggestion(run: WorkflowRun | undefined) {
  const projectId = run && isRunReady(run) ? run.project_id : "";
  const artifactId = run && isRunReady(run) ? run.artifacts.suggestion : "";
  const [state, setState] = useState<{ key: string; selection?: SuggestionSelection; error?: string }>({ key: "" });
  const key = projectId && artifactId ? `${projectId}:${artifactId}` : "";

  useEffect(() => {
    if (!key) return;
    let cancelled = false;
    api.suggestion(projectId, artifactId)
      .then(envelope => { if (!cancelled) setState({ key, selection: envelope.document.selection }); })
      .catch(reason => { if (!cancelled) setState({ key, error: errorText(reason) }); });
    return () => { cancelled = true; };
  }, [key, projectId, artifactId]);

  const current = state.key === key ? state : { key };
  return { selection: current.selection, error: current.error, loading: !!key && !current.selection && !current.error };
}

import { useCallback, useEffect, useState } from "react";
import { parseHotwords } from "../../editor/hotwords";
import { api, type ClipDefaults, type SuggestionBrief, type WorkflowRun } from "../../api";

export type RunOptions = {
  count: number;
  minimum: number;
  maximum: number;
  primarySubject: string;
  hotwordsText: string;
  instructions: string;
  newClipsOnly: boolean;
};

// o último texto usado fica salvo no navegador.
export const INSTRUCTIONS_KEY = "cortex-editorial-instructions";
export const DEFAULT_INSTRUCTIONS = [
  "Público: seguidores do @dayon.news, interessados em espiritualidade, consciência, ETs e ufologia, mistérios antigos, saúde e crítica ao sistema.",
  "Priorize, nesta ordem: 1) afirmações fortes ou revelações que desafiam o senso comum; 2) histórias pessoais com emoção ou virada; 3) explicações de um conceito complexo em poucas frases; 4) frases de efeito que dá vontade de compartilhar.",
  "Evite: apresentações e bastidores do programa, piadas internas, trechos que dependem de algo dito antes, e momentos em que o entrevistador fala mais que eu.",
  "Entre dois trechos parecidos, escolha o de gancho mais forte e o de final mais marcante.",
].join("\n");

function storedInstructions() {
  try { return localStorage.getItem(INSTRUCTIONS_KEY) || DEFAULT_INSTRUCTIONS; } catch { return DEFAULT_INSTRUCTIONS; }
}

export function saveInstructions(value: string) {
  try { localStorage.setItem(INSTRUCTIONS_KEY, value); } catch { /* storage indisponível: só perde a lembrança */ }
}

// Valores usados até o servidor responder; depois valem os de config/cortex.yaml.
const FALLBACK_DEFAULTS: ClipDefaults = { count: 10, minimum_seconds: 40, maximum_seconds: 120 };
let clipDefaults = FALLBACK_DEFAULTS;
let clipDefaultsRequest: Promise<ClipDefaults> | null = null;

/** Fetches the server's clip defaults once; a failed request is retried on the next call. */
export function loadClipDefaults(): Promise<ClipDefaults> {
  clipDefaultsRequest ??= Promise.resolve().then(() => api.clipDefaults()).then(result => {
    if (result && result.count > 0) clipDefaults = result;
    return clipDefaults;
  }).catch(() => { clipDefaultsRequest = null; return clipDefaults; });
  return clipDefaultsRequest;
}

export function resetClipDefaults() { clipDefaults = FALLBACK_DEFAULTS; clipDefaultsRequest = null; }

export function defaultRunOptions(): RunOptions {
  void loadClipDefaults();
  return { count: clipDefaults.count, minimum: clipDefaults.minimum_seconds, maximum: clipDefaults.maximum_seconds, primarySubject: "Dayon", hotwordsText: "", instructions: storedInstructions(), newClipsOnly: true };
}

export function useRunOptions(initial?: Partial<RunOptions>) {
  const [options, setOptionsState] = useState<RunOptions>(() => ({ ...defaultRunOptions(), ...initial }));
  const setOptions = useCallback((next: RunOptions) => {
    setOptionsState(current => {
      if (next.instructions !== current.instructions) saveInstructions(next.instructions);
      return next;
    });
  }, []);
  useEffect(() => {
    let live = true;
    void loadClipDefaults().then(loaded => {
      if (!live) return;
      setOptionsState(current => current.count === FALLBACK_DEFAULTS.count && current.minimum === FALLBACK_DEFAULTS.minimum_seconds && current.maximum === FALLBACK_DEFAULTS.maximum_seconds && !initial
        ? { ...current, count: loaded.count, minimum: loaded.minimum_seconds, maximum: loaded.maximum_seconds } : current);
    });
    return () => { live = false; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return [options, setOptions] as const;
}

/** Options that reproduce a previous run's brief, keeping the saved editorial direction. */
export function optionsFromRun(run: WorkflowRun | undefined, subject: string): Partial<RunOptions> {
  const brief = run?.brief;
  return {
    primarySubject: subject || brief?.primary_subject || "Dayon",
    ...(brief ? { count: brief.count, minimum: brief.minimum_seconds, maximum: brief.maximum_seconds } : {}),
  };
}

export function briefOf(options: RunOptions): SuggestionBrief & { hotwords: string[] } {
  return {
    count: options.count, minimum_seconds: options.minimum, maximum_seconds: options.maximum,
    instructions: options.instructions, primary_subject: options.primarySubject,
    new_clips_only: options.newClipsOnly, hotwords: parseHotwords(options.hotwordsText),
  };
}

export function hotwordCount(text: string) { return parseHotwords(text).length; }

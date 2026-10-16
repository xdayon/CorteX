import { useCallback, useState } from "react";
import { parseHotwords } from "../../editor/hotwords";
import type { SuggestionBrief, WorkflowRun } from "../../api";

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

export function defaultRunOptions(): RunOptions {
  return { count: 10, minimum: 40, maximum: 120, primarySubject: "Dayon", hotwordsText: "", instructions: storedInstructions(), newClipsOnly: true };
}

export function useRunOptions(initial?: Partial<RunOptions>) {
  const [options, setOptionsState] = useState<RunOptions>(() => ({ ...defaultRunOptions(), ...initial }));
  const setOptions = useCallback((next: RunOptions) => {
    setOptionsState(current => {
      if (next.instructions !== current.instructions) saveInstructions(next.instructions);
      return next;
    });
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

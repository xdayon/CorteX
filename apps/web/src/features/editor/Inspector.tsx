import { useEffect, useMemo, useState } from "react";
import { api, type CaptionCorrection, type ClipRow, type OverlayPreview, type RenderSettings, type WorkflowRun } from "../../api";
import { flatWords, type IndexedWord } from "../../editor/captions";
import { Button, Icon, Tabs, type IconName } from "../../ui";
import { rowId } from "../clips/clipModel";
import { CaptionTab } from "./CaptionTab";
import { FramingTab } from "./FramingTab";
import { HeadlineTab } from "./HeadlineTab";
import { ScenesTab } from "./ScenesTab";
import { ScopeBar } from "./ScopeBar";
import { StyleTab, type SettingsUpdate } from "./StyleTab";
import type { Scope } from "./useEditorSession";
import type { SceneData } from "./useSceneSuggestions";

type Tab = "caption" | "headline" | "scenes" | "framing" | "style";
/** Editing order: the two essential steps first, then the optional ones. */
const TABS: Array<{ value: Tab; label: string; icon: IconName; hint: string }> = [
  { value: "caption", label: "Legenda", icon: "captions", hint: "essencial — revise as palavras duvidosas" },
  { value: "headline", label: "Manchete", icon: "headline", hint: "essencial — escolha a manchete" },
  { value: "scenes", label: "Cenas", icon: "film", hint: "opcional, recomendado — reações e plano aberto deixam o corte dinâmico" },
  { value: "framing", label: "Enquadrar", icon: "crop", hint: "opcional — o padrão já funciona" },
  { value: "style", label: "Estilo", icon: "sliders", hint: "opcional — vale para todos os cortes" },
];

function useTranscriptWords(projectId: string, artifactId: string) {
  const [words, setWords] = useState<IndexedWord[] | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setWords(null); setError("");
    void Promise.resolve(api.transcript(projectId, artifactId, controller.signal))
      .then(result => { if (!controller.signal.aborted && result) setWords(flatWords(result.document.segments)); })
      .catch(reason => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [projectId, artifactId]);
  return { words, error };
}

export type InspectorProps = {
  row: ClipRow; rows: ClipRow[]; runs: Record<string, WorkflowRun>; settings: RenderSettings;
  update: SettingsUpdate; updateAll: SettingsUpdate; updateClip: SettingsUpdate; episodeId: string; scenes: SceneData; planId: string | null; onTeaser: (on: boolean) => void;
  scope: Scope; onScope: (scope: Scope) => void; customized: boolean; onReset: () => void;
  overlay: OverlayPreview | null; overlayLoading: boolean; overlayError: string; time: number; onSeek: (seconds: number) => void;
  captionEdits: Record<string, CaptionCorrection[]>; onCaptionEdits: (key: string, edits: CaptionCorrection[]) => void;
  onAllCaptionEdits: (all: Record<string, CaptionCorrection[]>) => void;
  headlines: Record<string, string>; onHeadline: (key: string, text: string | null) => void;
  emphasis: number[]; onEmphasis: (key: string, indices: number[]) => void;
  lineBreaks: number[]; onLineBreaks: (key: string, indices: number[]) => void; disabled: boolean;
};

export function Inspector(props: InspectorProps) {
  const { row, rows, runs, settings } = props;
  const [tab, setTab] = useState<Tab>("caption");
  const key = rowId(row);
  const transcript = runs[row.run_id].artifacts.transcript;
  const { words, error: wordsError } = useTranscriptWords(row.project_id, transcript);
  const targets = useMemo(() => rows.filter(item => runs[item.run_id].artifacts.transcript === transcript).map(item => ({ key: rowId(item), clip: item.clip })), [rows, runs, transcript]);
  const scoped = tab === "style" || tab === "framing";
  const step = TABS.findIndex(item => item.value === tab);
  const next = TABS[step + 1];

  return <section className="insp" aria-label="Ajustes do corte">
    <Tabs<Tab> label="Seções de ajuste" value={tab} onChange={setTab}
      options={TABS.map(item => ({ value: item.value, label: <><Icon name={item.icon} />{item.label}</> }))} />
    <p className="insp-step">Passo {step + 1} de {TABS.length} · {TABS[step].hint}</p>
    <div className="insp-body" role="tabpanel">
      {scoped && <ScopeBar scope={props.scope} onScope={props.onScope} count={rows.length} customized={props.customized} onReset={props.onReset} />}
      {tab === "caption" && <CaptionTab current={{ key, clip: row.clip }} targets={targets} overlay={props.overlay} loading={props.overlayLoading} error={props.overlayError}
        time={props.time} onSeek={props.onSeek} disabled={props.disabled}
        corrections={props.captionEdits[key] ?? []} onCorrections={edits => props.onCaptionEdits(key, edits)}
        emphasis={props.emphasis} onEmphasis={indices => props.onEmphasis(key, indices)}
        lineBreaks={props.lineBreaks} onLineBreaks={indices => props.onLineBreaks(key, indices)}
        captionEdits={props.captionEdits} onAllCaptionEdits={props.onAllCaptionEdits} words={words} wordsError={wordsError} />}
      {tab === "style" && <StyleTab settings={settings} update={props.update} disabled={props.disabled} />}
      {tab === "framing" && <FramingTab settings={settings} update={props.update} disabled={props.disabled} />}
      {tab === "scenes" && <ScenesTab scenes={props.scenes} planId={props.planId} entryId={props.episodeId} settings={settings} update={props.updateClip} onSeek={props.onSeek} disabled={props.disabled} />}
      {tab === "headline" && <HeadlineTab clip={row.clip} settings={settings} update={props.updateAll} disabled={props.disabled}
        headline={props.headlines[key]} onHeadline={text => props.onHeadline(key, text)} onTeaser={props.onTeaser} />}
      {next && <div className="insp-next"><Button variant="ghost" onClick={() => setTab(next.value)}>Próximo: {next.label} <Icon name="arrowRight" small /></Button></div>}
    </div>
  </section>;
}

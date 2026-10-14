import { useEffect, useRef, useState } from "react";
import { api, type CaptionCorrection, RenderSettings, SuggestedClip, WorkflowRun } from "../api";
import { clipDuration, clipKey, effectiveHeadline } from "./clips";
import { emphasisFor, flatWords, type IndexedWord } from "./captions";
import { CaptionTab } from "./CaptionTab";
import { FramingTab } from "./FramingTab";
import { HeadlineTab } from "./HeadlineTab";
import { PreviewPlayer, type PlayerHandle } from "./PreviewPlayer";
import { StyleTab, type SettingsUpdate } from "./StyleTab";
import { useDraftPreview } from "./useDraftPreview";
import { useOverlayPreview } from "./useOverlayPreview";

type Tab = "caption" | "style" | "framing" | "headline";
const TABS: Array<{ id: Tab; label: string }> = [
  { id: "caption", label: "Legenda" }, { id: "style", label: "Estilo" },
  { id: "framing", label: "Enquadramento" }, { id: "headline", label: "Manchete" },
];
const EMPTY_CORRECTIONS: CaptionCorrection[] = [];
const EMPTY_NUMBERS: number[] = [];

const STATUS_LABELS: Record<string, string> = {
  queued: "Na fila", planning: "Montando", camera_planning: "Montando", rendering: "Exportando",
  succeeded: "Pronto", failed: "Falhou", cancelled: "Cancelado",
};

export type ExportEditorProps = {
  run: WorkflowRun;
  clips: SuggestedClip[];
  focusedKey: string;
  onFocus: (key: string) => void;
  settings: RenderSettings;
  update: SettingsUpdate;
  captionEdits: Record<string, CaptionCorrection[]>;
  onCaptionEdits: (key: string, edits: CaptionCorrection[]) => void;
  onAllCaptionEdits: (all: Record<string, CaptionCorrection[]>) => void;
  headlines: Record<string, string>;
  onHeadline: (key: string, text: string | null) => void;
  emphasisEdits: Record<string, number[]>;
  onEmphasis: (key: string, indices: number[]) => void;
  sceneIndexArtifactId?: string | null;
  itemStatus: Record<string, string>;
  disabled: boolean;
};

export function ExportEditor(props: ExportEditorProps) {
  const { run, clips, settings, update, disabled } = props;
  const clip = clips.find(item => clipKey(item) === props.focusedKey) ?? clips[0];
  const key = clip ? clipKey(clip) : "";
  const [tab, setTab] = useState<Tab>("caption");
  const player = useRef<PlayerHandle>(null);
  const [time, setTime] = useState(0);
  const [words, setWords] = useState<IndexedWord[] | null>(null);
  const [wordsError, setWordsError] = useState("");
  useEffect(() => {
    const controller = new AbortController();
    setWords(null); setWordsError("");
    void api.transcript(run.project_id, run.artifacts.transcript, controller.signal)
      .then(({ document }) => { if (!controller.signal.aborted) setWords(flatWords(document.segments)); })
      .catch(reason => { if (!controller.signal.aborted) setWordsError(reason instanceof Error ? reason.message : String(reason)); });
    return () => controller.abort();
  }, [run.project_id, run.artifacts.transcript]);
  const preview = useDraftPreview({ projectId: run.project_id, run, clip, settings, sceneIndexArtifactId: props.sceneIndexArtifactId });
  const corrections = props.captionEdits[key] ?? EMPTY_CORRECTIONS;
  const emphasis = clip ? emphasisFor(clip, props.emphasisEdits) : EMPTY_NUMBERS;
  const { overlay, error: overlayError, loading: overlayLoading } = useOverlayPreview({
    projectId: run.project_id, editPlanArtifactId: preview.editPlanArtifactId, settings,
    corrections, emphasis, headlineText: clip ? effectiveHeadline(clip, props.headlines[key]) : "",
  });
  if (!clip) return null;
  return <div className="export-editor">
    <nav className="clip-rail" aria-label="Cortes selecionados">
      {clips.map(item => {
        const itemKey = clipKey(item);
        const status = props.itemStatus[itemKey];
        return <button key={itemKey} type="button" className={`rail-clip ${itemKey === key ? "active" : ""}`}
          aria-pressed={itemKey === key} onClick={() => props.onFocus(itemKey)}>
          <b>{item.title}</b>
          <span>{clipDuration(item.estimated_duration)}{status ? ` · ${STATUS_LABELS[status] ?? status}` : ""}</span>
        </button>;
      })}
    </nav>
    <div className="editor-stage">
      <PreviewPlayer ref={player} preview={preview} overlay={overlay} onTime={setTime}/>
      {overlayError && <p className="preview-note warn" role="status">Não foi possível atualizar a legenda da prévia: {overlayError}</p>}
    </div>
    <section className="editor-panel" aria-label="Ajustes do corte">
      <div className="tab-bar" role="tablist" aria-label="Seções de ajuste">
        {TABS.map(item => <button key={item.id} type="button" role="tab" id={`tab-${item.id}`} aria-selected={tab === item.id}
          aria-controls="editor-tabpanel" className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>{item.label}</button>)}
      </div>
      <div id="editor-tabpanel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="tab-body">
        {tab === "caption" && <CaptionTab clip={clip} clips={clips} overlay={overlay} loading={overlayLoading} error={overlayError}
          time={time} onSeek={seconds => player.current?.seek(seconds)} disabled={disabled}
          corrections={corrections} onCorrections={edits => props.onCaptionEdits(key, edits)}
          emphasis={emphasis} onEmphasis={indices => props.onEmphasis(key, indices)}
          captionEdits={props.captionEdits} onAllCaptionEdits={props.onAllCaptionEdits} words={words} wordsError={wordsError}/>}
        {tab === "style" && <StyleTab settings={settings} update={update} disabled={disabled}/>}
        {tab === "framing" && <FramingTab settings={settings} update={update} disabled={disabled}/>}
        {tab === "headline" && <HeadlineTab clip={clip} settings={settings} update={update} disabled={disabled}
          headline={props.headlines[key] ?? ""} onHeadline={text => props.onHeadline(key, text)}/>}
      </div>
    </section>
  </div>;
}

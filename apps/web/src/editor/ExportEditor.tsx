import { useRef, useState } from "react";
import type { CaptionCorrection, RenderSettings, SuggestedClip, WorkflowRun } from "../api";
import { TranscriptEditor } from "../TranscriptEditor";
import { clipDuration, clipKey, effectiveHeadline } from "./clips";
import { emphasisFor } from "./captions";
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
  const preview = useDraftPreview({ projectId: run.project_id, run, clip, settings, sceneIndexArtifactId: props.sceneIndexArtifactId });
  const corrections = props.captionEdits[key] ?? EMPTY_CORRECTIONS;
  const emphasis = clip ? emphasisFor(clip, props.emphasisEdits) : EMPTY_NUMBERS;
  const { overlay, error: overlayError } = useOverlayPreview({
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
      <PreviewPlayer ref={player} preview={preview} overlay={overlay}/>
      {overlayError && <p className="preview-note warn" role="status">Não foi possível atualizar a legenda da prévia: {overlayError}</p>}
    </div>
    <section className="editor-panel" aria-label="Ajustes do corte">
      <div className="tab-bar" role="tablist" aria-label="Seções de ajuste">
        {TABS.map(item => <button key={item.id} type="button" role="tab" id={`tab-${item.id}`} aria-selected={tab === item.id}
          aria-controls="editor-tabpanel" className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}>{item.label}</button>)}
      </div>
      <div id="editor-tabpanel" role="tabpanel" aria-labelledby={`tab-${tab}`} className="tab-body">
        {tab === "caption" && <TranscriptEditor projectId={run.project_id} artifactId={run.artifacts.transcript} clip={clip}
          corrections={corrections} disabled={disabled} onChange={edits => props.onCaptionEdits(key, edits)}/>}
        {tab === "style" && <StyleTab settings={settings} update={update} disabled={disabled}/>}
        {tab === "framing" && <FramingTab settings={settings} update={update} disabled={disabled}/>}
        {tab === "headline" && <HeadlineTab clip={clip} settings={settings} update={update} disabled={disabled}
          headline={props.headlines[key] ?? ""} onHeadline={text => props.onHeadline(key, text)}/>}
      </div>
    </section>
  </div>;
}

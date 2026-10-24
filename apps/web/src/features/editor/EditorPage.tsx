import { useCallback, useEffect, useRef, useState } from "react";
import { type ExportItem } from "../../api";
import { plural } from "../../app/humanize";
import { usePageTitle } from "../../app/usePageTitle";
import { effectiveHeadline } from "../../editor/clips";
import { useOverlayPreview } from "../../editor/useOverlayPreview";
import { episodePath, Link } from "../../router";
import { Button, Dialog, EmptyState, Icon, Select, Skeleton, useToast } from "../../ui";
import { headlineOf, rowId } from "../clips/clipModel";
import { ClipRail } from "./ClipRail";
import { ExportBatchPanel } from "./ExportBatchPanel";
import { Inspector } from "./Inspector";
import { isCustomized } from "./overrides";
import type { PlayerHandle } from "./PreviewPlayer";
import { Stage } from "./Stage";
import { qualify } from "./loadEditor";
import { useEditorSession } from "./useEditorSession";
import { useEditorShortcuts } from "./useEditorShortcuts";
import { usePreviewQueue } from "./usePreviewQueue";
import { useSceneSuggestions } from "./useSceneSuggestions";
import "./editor.css";

const EMPTY_NUMBERS: number[] = [];
const SAVE_TEXT = { saving: "Salvando…", saved: "Alterações salvas", error: "Não salvo no servidor" };

export function EditorPage({ id, clipKeys, run = "" }: { id: string; clipKeys: string[]; run?: string }) {
  const toast = useToast();
  const session = useEditorSession(id, clipKeys, run);
  usePageTitle("Editor · CorteX");
  const { exportError, setExportError } = session;
  useEffect(() => { if (exportError) { toast(exportError, "bad"); setExportError(""); } }, [exportError, setExportError, toast]);

  const back = <Link to={episodePath(id)} className="back"><Icon name="arrowLeft" small />Voltar ao episódio</Link>;
  if (session.phase.name === "loading") return <div className="ed-page">{back}<Skeleton height={44} /><Skeleton height={420} /></div>;
  if (session.phase.name === "error") return <div className="ed-page">{back}
    <EmptyState title="Não foi possível abrir o editor" action={<Link to={episodePath(id)} className="btn">Voltar ao episódio</Link>}>{session.phase.message}</EmptyState>
    {session.encoderError && <p role="alert" className="notice bad">{session.encoderError}</p>}</div>;
  return <Studio id={id} session={session} />;
}

function Studio({ id, session }: { id: string; session: ReturnType<typeof useEditorSession> }) {
  const { rows, runs, focusedKey, setFocusedKey, batches, busy } = session;
  const [time, setTime] = useState(0);
  const [showBatch, setShowBatch] = useState(false);
  const player = useRef<PlayerHandle>(null);
  const onTime = useCallback((seconds: number) => setTime(previous => Math.abs(previous - seconds) >= 0.04 ? seconds : previous), []);

  const { previews, retry } = usePreviewQueue({
    rows, runs, focusedKey, enabled: session.ready, settingsFor: session.settingsFor, sceneFor: session.sceneFor,
  });
  const row = rows.find(item => rowId(item) === focusedKey) ?? rows[0];
  const key = rowId(row);
  const settings = session.settingsFor(key);
  const preview = previews[key];
  const corrections = session.captionEdits[key] ?? [];
  const emphasis = session.emphasisEdits[key] ?? row.clip.emphasis_word_indices ?? EMPTY_NUMBERS;
  const lineBreaks = session.lineBreakEdits[key] ?? EMPTY_NUMBERS;
  const overlay = useOverlayPreview({
    projectId: row.project_id, editPlanArtifactId: preview?.planId ?? null, settings, corrections, emphasis, lineBreaks,
    headlineText: effectiveHeadline(row.clip, session.headlines[key]),
  });

  const scenes = useSceneSuggestions(id, preview?.planId ?? null);
  useEffect(() => { setTime(0); }, [key]);
  const focusIndex = rows.findIndex(item => rowId(item) === key);
  const focus = useCallback((next: string) => setFocusedKey(next), [setFocusedKey]);

  const chosen = new Set(rows.map(rowId));
  const batchList = Object.values(batches).map(batch => ({ ...batch, items: batch.items.filter(item => chosen.has(qualify(batch.workflow_run_id, item.clip_key))) })).filter(batch => batch.items.length);
  const exportStatus: Record<string, ExportItem["status"]> = Object.fromEntries(batchList.flatMap(batch =>
    batch.items.map(item => [qualify(batch.workflow_run_id, item.clip_key), item.status] as const)));
  const label = plural(rows.length, "corte", "cortes");
  const exportAll = useCallback(() => { if (busy) return; setShowBatch(true); void session.exportSelected(); }, [busy, session]);

  useEditorShortcuts({
    enabled: true, togglePlay: () => player.current?.toggle(), skip: delta => player.current?.skip(delta),
    moveClip: step => { const next = rows[Math.min(Math.max(focusIndex + step, 0), rows.length - 1)]; if (next) focus(rowId(next)); },
    exportAll,
  });

  return <div className="studio">
    <header className="studio-head">
      <Link to={episodePath(id)} className="back"><Icon name="arrowLeft" small />Voltar ao episódio</Link>
      <h1>Editor · {label}</h1>
      <Select className="clip-select" aria-label="Corte em edição" value={key} onChange={event => focus(event.target.value)}>
        {rows.map(item => <option key={rowId(item)} value={rowId(item)}>{headlineOf(item)}</option>)}
      </Select>
      <div className="grow" />
      <span className={`saved ${session.saveState}`} role="status">{session.saveState === "saved" && <Icon name="check" small />}{SAVE_TEXT[session.saveState]}</span>
      {batchList.length > 0 && !busy && <Button variant="ghost" onClick={() => setShowBatch(true)}>Ver exportação</Button>}
      <Button variant="primary" icon="download" disabled={busy || !rows.length} onClick={exportAll}>
        {busy ? `Exportando… ${Math.round(session.exportProgress)}%` : `Exportar ${label}`}
      </Button>
    </header>
    {session.notice && <p className="notice" role="status">{session.notice}</p>}
    {session.warnings.length > 0 && <aside className="notice" role="status" aria-label="Avisos da exportação"><div>{session.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div></aside>}
    {session.draftError && <p role="alert" className="notice bad">{session.draftError}</p>}
    {session.encoderError && <p role="alert" className="notice bad">{session.encoderError}</p>}
    <div className="ed">
      <ClipRail episodeId={id} rows={rows} previews={previews} focusedKey={key} onFocus={focus} exportStatus={exportStatus}
        customized={clip => isCustomized(session.overrides, clip)} />
      <Stage ref={player} row={row} preview={preview} overlay={overlay.overlay} overlayError={overlay.error} settings={settings} scenes={scenes.data}
        corrections={corrections} time={time} onTime={onTime} onRetry={() => retry(key)} />
      <Inspector row={row} rows={rows} runs={runs} settings={settings} update={change => session.edit(change)} updateAll={change => session.edit(change, "all")}
        updateClip={change => session.edit(change, "clip")} scenes={scenes} planId={preview?.planId ?? null} episodeId={id}
        onTeaser={on => session.edit(s => ({ ...s, transitions: { ...s.transitions, teaser: on } }), "clip")}
        scope={session.scope} onScope={session.setScope} customized={isCustomized(session.overrides, key)} onReset={() => session.resetOverride(key)}
        overlay={overlay.overlay} overlayLoading={overlay.loading} overlayError={overlay.error} time={time} onSeek={seconds => player.current?.seek(seconds)}
        captionEdits={session.captionEdits} onCaptionEdits={session.setCaptionFor} onAllCaptionEdits={session.setCaptionEdits}
        headlines={session.headlines} onHeadline={session.setHeadlineFor} emphasis={emphasis} onEmphasis={session.setEmphasisFor}
        lineBreaks={lineBreaks} onLineBreaks={session.setLineBreaksFor} disabled={busy} />
    </div>
    {showBatch && batchList.length > 0 && <Dialog title="Exportação" wide onClose={() => setShowBatch(false)}>
      <ExportBatchPanel batches={batchList} rows={rows} busy={busy} progress={session.exportProgress} onCancel={session.cancelBatches} onRetry={session.retryBatches} />
    </Dialog>}
  </div>;
}

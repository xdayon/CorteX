import { useEffect } from "react";
import { plural } from "../../app/humanize";
import { usePageTitle } from "../../app/usePageTitle";
import { clipKey } from "../../editor/clips";
import { ExportEditor } from "../../editor/ExportEditor";
import { episodePath, Link } from "../../router";
import { Button, EmptyState, Icon, Skeleton, useToast } from "../../ui";
import { ExportBatchPanel } from "./ExportBatchPanel";
import { useEditorSession } from "./useEditorSession";
import "./editor.css";

export function EditorPage({ id, runId, clipKeys }: { id: string; runId: string; clipKeys: string[] }) {
  const toast = useToast();
  const session = useEditorSession(id, runId, clipKeys);
  const { run, selection, busy } = session;
  usePageTitle("Editor · CorteX");
  const { exportError, setExportError } = session;
  useEffect(() => { if (exportError) { toast(exportError, "bad"); setExportError(""); } }, [exportError, setExportError, toast]);

  const back = <Link to={episodePath(id)} className="back"><Icon name="arrowLeft" small />Voltar ao episódio</Link>;
  if (session.phase.name === "loading") return <div className="ed-page">{back}<Skeleton height={44} /><Skeleton height={420} /></div>;
  if (session.phase.name === "error") return <div className="ed-page">{back}
    <EmptyState title="Não foi possível abrir o editor" action={<Link to={episodePath(id)} className="btn">Voltar ao episódio</Link>}>{session.phase.message}</EmptyState>
    {session.encoderError && <p role="alert" className="notice bad">{session.encoderError}</p>}</div>;
  if (!run || !selection) return null;

  const active = selection.clips.filter(clip => session.selectedKeys.includes(clipKey(clip)));
  const label = plural(active.length, "corte", "cortes");
  return <div className="ed-page">
    {back}
    <header className="ed-head">
      <h1>Editor · {label}</h1>
      <div className="grow" />
      <Button variant="primary" size="lg" icon="play" disabled={busy || !active.length} onClick={() => void session.exportSelected()}>
        {busy ? "Exportando…" : `Exportar ${label}`}
      </Button>
    </header>
    {session.notice && <p className="notice" role="status">{session.notice}</p>}
    <ExportEditor run={run} clips={active} focusedKey={session.focusedKey} onFocus={session.setFocusedKey} settings={session.settings} update={session.setSettings}
      captionEdits={session.captionEdits} onCaptionEdits={(key, edits) => session.setCaptionEdits(current => ({ ...current, [key]: edits }))} onAllCaptionEdits={session.setCaptionEdits}
      headlines={session.headlines} onHeadline={(key, text) => session.setHeadlines(current => { const next = { ...current }; if (text) next[key] = text; else delete next[key]; return next; })}
      emphasisEdits={session.emphasisEdits} onEmphasis={(key, indices) => session.setEmphasisEdits(current => ({ ...current, [key]: indices }))}
      sceneIndexArtifactId={session.visualArtifacts?.scene} disabled={busy}
      itemStatus={Object.fromEntries((session.activeBatch?.items ?? []).map(item => [item.clip_key, item.status]))} />
    {session.activeBatch && <ExportBatchPanel batch={session.activeBatch} clips={selection.clips} episodeId={id} busy={busy} progress={session.renderProgress}
      onCancel={session.cancelBatch} onRetry={session.retryBatch} />}
    {session.warnings.length > 0 && <aside className="notice" role="status" aria-label="Avisos da exportação"><div>{session.warnings.map((warning, index) => <p key={index}>{warning}</p>)}</div></aside>}
    {session.draftError && <p role="alert" className="notice bad">{session.draftError}</p>}
    {session.encoderError && <p role="alert" className="notice bad">{session.encoderError}</p>}
  </div>;
}

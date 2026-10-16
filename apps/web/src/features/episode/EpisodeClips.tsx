import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ClipRow, ClipStatus } from "../../api";
import { EmptyState, Segmented, Select, Skeleton } from "../../ui";
import { BulkBar } from "../clips/BulkBar";
import { PublicationDialog } from "../clips/PublicationDialog";
import { rowId, shortDate, topScores } from "../clips/clipModel";
import { useClipCommands } from "../clips/useClipCommands";
import { ClipDetail } from "./ClipDetail";
import { ClipRowList } from "./ClipRowList";
import { OldExports } from "./OldExports";
import type { PlayerHandle } from "./SourcePiecesPlayer";
import { useClipShortcuts } from "./useClipShortcuts";
import { useEpisodeClips } from "./useEpisodeClips";
import { useSearchParams } from "../../router";

type StatusFilter = "all" | ClipStatus;
type Sort = "score" | "order" | "recent";

const STATUS_OPTIONS: Array<[StatusFilter, string]> = [
  ["all", "Todos"], ["new", "Novos"], ["approved", "Aprovados"], ["exported", "Exportados"], ["saved", "Salvos"], ["posted", "Postados"], ["discarded", "Descartados"],
];

const textOf = (row: ClipRow) => `${row.headline} ${row.title} ${(row.clip.pieces ?? []).map(piece => piece.text).join(" ")}`.toLocaleLowerCase("pt-BR");

export function sortRows(rows: ClipRow[], sort: Sort) {
  const byScore = (a: ClipRow, b: ClipRow) => (b.score ?? 0) - (a.score ?? 0) || a.rank - b.rank;
  const sorter: Record<Sort, (a: ClipRow, b: ClipRow) => number> = {
    score: byScore,
    order: (a, b) => a.start_second - b.start_second || a.run_created_at.localeCompare(b.run_created_at),
    recent: (a, b) => b.run_created_at.localeCompare(a.run_created_at) || byScore(a, b),
  };
  return [...rows].sort(sorter[sort]);
}

export function EpisodeClips({ episodeId, refreshKey }: { episodeId: string; refreshKey: string }) {
  const [params, setParams] = useSearchParams();
  const { data, rows, setRows, loading, error } = useEpisodeClips(episodeId, refreshKey);
  const commands = useClipCommands<ClipRow>(setRows, useCallback(() => episodeId, [episodeId]));
  const [selected, setSelected] = useState<string[]>([]);
  const [publishing, setPublishing] = useState<string | null>(null);
  const player = useRef<PlayerHandle>(null);
  const discard = useRef<{ open: () => void }>(null);

  const status = (params.get("status") as StatusFilter) || "all";
  const run = params.get("run") || "";
  const query = params.get("q") || "";
  const sort = (params.get("sort") as Sort) || "score";
  const clipParam = params.get("clip") || "";

  const inRun = useMemo(() => rows.filter(row => !run || row.run_id === run), [rows, run]);
  const counts = useMemo(() => {
    const result: Record<string, number> = { all: inRun.length };
    for (const row of inRun) result[row.status] = (result[row.status] ?? 0) + 1;
    return result;
  }, [inRun]);
  const visible = useMemo(() => {
    const search = query.trim().toLocaleLowerCase("pt-BR");
    return sortRows(inRun.filter(row => (status === "all" || row.status === status) && (!search || textOf(row).includes(search))), sort);
  }, [inRun, status, query, sort]);

  const topScore = useMemo(() => topScores(rows), [rows]);
  const activeId = visible.some(row => rowId(row) === clipParam) ? clipParam : visible[0] ? rowId(visible[0]) : "";
  const active = visible.find(row => rowId(row) === activeId);
  const selectedRows = rows.filter(row => selected.includes(rowId(row)));
  useEffect(() => setSelected(current => current.filter(id => rows.some(row => rowId(row) === id))), [rows]);

  const move = useCallback((step: number) => {
    if (!visible.length) return;
    const index = Math.max(0, visible.findIndex(row => rowId(row) === activeId));
    const next = visible[Math.min(Math.max(index + step, 0), visible.length - 1)];
    setParams({ clip: rowId(next) }, { replace: true });
  }, [visible, activeId, setParams]);
  const toggleSelect = useCallback((id: string) => setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]), []);

  useClipShortcuts({
    enabled: !!active && publishing === null,
    move, togglePlay: () => player.current?.toggle(), select: () => active && toggleSelect(rowId(active)),
    approve: () => active && void commands.approve([active]), discard: () => discard.current?.open(),
    save: () => active && void commands.toggleSaved([active]), edit: () => active && commands.edit([active]),
  });

  if (loading) return <div className="clips-loading" role="status" aria-label="Carregando cortes">{[0, 1, 2, 3].map(index => <Skeleton key={index} height={52} />)}</div>;
  if (error) return <div className="notice bad" role="alert"><p>Não foi possível carregar os cortes: {error}</p></div>;
  const runs = data?.runs ?? [];
  const publishRow = rows.find(row => rowId(row) === publishing);

  return <>
    {!rows.length && !(data?.other_exports.length) ? <EmptyState title="Nenhum corte ainda">Gere uma seleção para ver aqui os cortes sugeridos, exportados e postados.</EmptyState> : rows.length > 0 && <section className="ep2" aria-label="Cortes do episódio">
      <div className="ep2-list">
        <div className="ep2-tools">
          <Segmented label="Filtrar por situação" value={status} onChange={value => setParams({ status: value === "all" ? null : value })}
            options={STATUS_OPTIONS.map(([value, label]) => ({ value, label, count: counts[value] ?? 0 }))} />
          <div className="ep2-tools-row">
            <Select aria-label="Seleção" value={run} onChange={event => setParams({ run: event.target.value || null })}>
              <option value="">Todas as seleções</option>
              {runs.map(item => <option key={item.id} value={item.id}>Seleção de {shortDate(item.created_at)} · {item.clip_count} cortes</option>)}
            </Select>
            <input className="input" type="search" aria-label="Buscar corte" placeholder="Buscar manchete ou texto" value={query} onChange={event => setParams({ q: event.target.value || null }, { replace: true })} />
          </div>
          <Segmented label="Ordenar cortes" value={sort} onChange={value => setParams({ sort: value === "score" ? null : value })}
            options={[{ value: "score", label: "Nota" }, { value: "order", label: "Ordem no episódio" }, { value: "recent", label: "Mais recentes" }]} />
        </div>
        {visible.length
          ? <ClipRowList rows={visible} topScore={topScore} activeId={activeId} selected={selected} showRunDate={!run && runs.length > 1} dimDiscarded={status === "all"}
              onActivate={id => setParams({ clip: id }, { replace: true })} onToggle={toggleSelect} />
          : <EmptyState title="Nenhum corte aqui">Mude o filtro ou a busca para ver outros cortes.</EmptyState>}
      </div>
      <div className="ep2-detail">
        {active && <ClipDetail key={rowId(active)} ref={player} topScore={topScore(active)} discardRef={discard} row={active}
          onApprove={() => void commands.approve([active])} onDiscard={reason => void commands.discard([active], reason)}
          onEdit={() => commands.edit([active])} onToggleSaved={() => void commands.toggleSaved([active])} onPublish={() => setPublishing(rowId(active))} />}
      </div>
    </section>}
    {!!data?.other_exports.length && <OldExports items={data.other_exports} />}
    <BulkBar rows={selectedRows} onClear={() => setSelected([])} onApprove={() => void commands.approve(selectedRows)}
      onDiscard={reason => void commands.discard(selectedRows, reason)} onSave={() => void commands.toggleSaved(selectedRows)}
      onDownload={() => void commands.download(selectedRows)} onEdit={() => commands.edit(selectedRows)} />
    {publishRow && <PublicationDialog row={publishRow} onClose={() => setPublishing(null)} onSaved={commands.published} onDeleted={commands.unpublished} />}
  </>;
}

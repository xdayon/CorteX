import { useCallback, useMemo, useState } from "react";
import { api, type BoardClip, type ClipStatus } from "../../api";
import { usePageTitle } from "../../app/usePageTitle";
import { clipDuration } from "../../editor/clips";
import { Link } from "../../router";
import { Button, buttonClass, Chip, EmptyState, Icon, Segmented, Select, Skeleton } from "../../ui";
import { BulkBar } from "../clips/BulkBar";
import { PublicationDialog } from "../clips/PublicationDialog";
import { SavedToggle } from "../clips/SavedToggle";
import { SelectAll } from "../clips/SelectAll";
import { headlineOf, newestExport, rowId, shortDate, STATUS_LABELS, STATUS_TONES } from "../clips/clipModel";
import { useClipCommands } from "../clips/useClipCommands";
import { useBoardClips } from "./useBoardClips";
import "./board.css";

type Tab = "post" | "approved" | "posted" | "discarded" | "all";
const TABS: Array<[Tab, string]> = [["post", "Para postar"], ["approved", "Aprovados"], ["posted", "Postados"], ["discarded", "Descartados"], ["all", "Todos"]];
const MATCH: Record<Tab, ClipStatus[] | null> = { post: ["exported", "saved"], approved: ["approved"], posted: ["posted"], discarded: ["discarded"], all: null };

function Poster({ row }: { row: BoardClip }) {
  const latest = newestExport(row);
  const fallback = api.sourcePreviewUrl(row.project_id, row.source_asset_id, row.start_second + 1);
  const [failed, setFailed] = useState(false);
  const src = latest && !failed ? api.posterUrl(row.project_id, latest.render_artifact_id) : fallback;
  return <img className="brow-poster" src={src} alt="" loading="lazy" onError={() => setFailed(true)} />;
}

const lastDate = (row: BoardClip) => newestExport(row)?.exported_at ?? row.run_created_at;

export function BoardPage() {
  usePageTitle("Cortes · CorteX");
  const { rows, setRows, loading, error } = useBoardClips();
  const commands = useClipCommands<BoardClip>(setRows, useCallback((row: BoardClip) => row.episode_id, []));
  const [tab, setTab] = useState<Tab>("post");
  const [episode, setEpisode] = useState("");
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [publishing, setPublishing] = useState<string | null>(null);

  const count = (statuses: ClipStatus[]) => rows.filter(row => statuses.includes(row.status)).length;
  const episodes = useMemo(() => [...new Map(rows.map(row => [row.episode_id, row.episode_title])).entries()], [rows]);
  const visible = useMemo(() => {
    const search = query.trim().toLocaleLowerCase("pt-BR");
    const wanted = MATCH[tab];
    return rows.filter(row => (!wanted || wanted.includes(row.status)) && (!episode || row.episode_id === episode)
      && (!search || `${row.headline} ${row.title} ${row.episode_title}`.toLocaleLowerCase("pt-BR").includes(search)))
      .sort((a, b) => lastDate(b).localeCompare(lastDate(a)));
  }, [rows, tab, episode, query]);
  const chosen = rows.filter(row => selected.includes(rowId(row)));
  const toggle = (id: string) => setSelected(current => current.includes(id) ? current.filter(item => item !== id) : [...current, id]);
  const publishRow = rows.find(row => rowId(row) === publishing);

  return <div className="board">
    <header className="board-head">
      <h1>Cortes</h1>
      <span className="lib-sub">{loading ? "Carregando…" : `${count(["exported", "saved"])} prontos para postar · ${count(["saved"])} salvos · ${count(["posted"])} postados`}</span>
    </header>
    <div className="board-tools">
      <Segmented label="Situação dos cortes" value={tab} onChange={setTab} options={TABS.map(([value, label]) => ({ value, label, count: MATCH[value] ? count(MATCH[value]!) : rows.length }))} />
      <div className="grow" />
      <Select aria-label="Episódio" value={episode} onChange={event => setEpisode(event.target.value)}>
        <option value="">Todos os episódios</option>
        {episodes.map(([id, title]) => <option key={id} value={id}>{title}</option>)}
      </Select>
      <input className="input board-search" type="search" aria-label="Buscar corte" placeholder="Buscar manchete ou episódio" value={query} onChange={event => setQuery(event.target.value)} />
    </div>
    {error && <div className="notice bad" role="alert"><p>Não foi possível carregar os cortes: {error}</p></div>}
    {loading && <div className="clips-loading" role="status" aria-label="Carregando cortes">{[0, 1, 2].map(index => <Skeleton key={index} height={96} />)}</div>}
    {!loading && !error && !visible.length && <EmptyState title="Nenhum corte aqui">{tab === "post" ? "Cortes exportados aparecem aqui até serem postados." : "Mude a aba ou o filtro para ver outros cortes."}</EmptyState>}
    {!loading && <SelectAll ids={visible.map(rowId)} selected={selected} onChange={setSelected} />}
    <ul className="board-rows">
      {visible.map(row => {
        const id = rowId(row);
        const latest = newestExport(row);
        const on = selected.includes(id);
        const link = `/episodios/${encodeURIComponent(row.episode_id)}?${new URLSearchParams({ clip: id })}`;
        return <li key={id} className={`brow${on ? " act" : ""}`}>
          <button type="button" role="checkbox" aria-checked={on} aria-label={`Selecionar ${headlineOf(row)}`} className={`ck${on ? " on" : ""}`} onClick={() => toggle(id)}>
            <span className="ckb"><Icon name="check" small /></span>
          </button>
          <Poster row={row} />
          <div className="brow-main">
            <span className="brow-title">{headlineOf(row)}</span>
            <span className="brow-ep">{row.episode_title}</span>
            <span className="brow-meta"><span className="mono">{clipDuration(row.duration)}</span> · {latest ? `exportado em ${shortDate(latest.exported_at)}` : `sugerido em ${shortDate(row.run_created_at)}`}</span>
          </div>
          <Chip tone={STATUS_TONES[row.status]}>{STATUS_LABELS[row.status]}</Chip>
          <div className="brow-actions">
            {latest && <a className={buttonClass("secondary", "sm")} href={latest.media_url} download>Baixar</a>}
            {latest && <SavedToggle saved={!!row.state?.saved} onClick={() => void commands.toggleSaved([row])} />}
            {latest && <Button size="sm" onClick={() => setPublishing(id)}>{row.publication ? "Editar publicação" : "Registrar publicação"}</Button>}
            <Link to={link} className={buttonClass("ghost", "sm")}>Abrir no episódio</Link>
          </div>
        </li>;
      })}
    </ul>
    <BulkBar rows={chosen} onClear={() => setSelected([])} onApprove={() => void commands.approve(chosen)} onDiscard={reason => void commands.discard(chosen, reason)}
      onSave={() => void commands.toggleSaved(chosen)} onDownload={() => void commands.download(chosen)} onEdit={() => commands.edit(chosen)} />
    {publishRow && <PublicationDialog row={publishRow} onClose={() => setPublishing(null)} onSaved={commands.published} onDeleted={commands.unpublished} />}
  </div>;
}

import type { CSSProperties, ReactNode } from "react";
import { Link, useLocation, type Route } from "../router";
import { IconButton, Menu, MenuLabel } from "../ui";
import type { ApiJob } from "../api";
import { ErrorBoundary } from "./ErrorBoundary";
import { jobLabel } from "./humanize";
import { ShortcutsProvider, useShortcutsDialog } from "./ShortcutsDialog";
import { useActivity } from "./useActivity";
import "./shell.css";

const percent = (job: ApiJob) => Math.round(job.progress ?? 0);

function ActivityPill() {
  const { active, lead, offline } = useActivity();
  if (offline) return <span className="activity-idle bad" role="status">Sem conexão com o CorteX</span>;
  if (!lead) return <span className="activity-idle">Tudo em dia</span>;
  const label = lead.status === "running" ? `${jobLabel(lead.type)} · ${percent(lead)}%` : "Na fila";
  return <Menu label="Tarefas em andamento" trigger={props => <button type="button" className="activity" {...props}>
    <span className="ring" style={{ "--p": `${percent(lead)}%` } as CSSProperties} aria-hidden="true" />{label}
  </button>}>
    <MenuLabel>{active.length === 1 ? "1 tarefa em andamento" : `${active.length} tarefas em andamento`}</MenuLabel>
    {active.map(job => <div key={job.id} className="activity-item" role="presentation">
      <b>{jobLabel(job.type)}</b><span className="mono">{job.status === "running" ? `${percent(job)}%` : "na fila"}</span>
    </div>)}
  </Menu>;
}

function ShortcutsButton() {
  const open = useShortcutsDialog();
  return <IconButton icon="keyboard" aria-label="Atalhos do teclado" title="Atalhos do teclado (?)" onClick={open} />;
}

export function Shell({ route, children }: { route: Route; children: ReactNode }) {
  const location = useLocation();
  const inEpisodes = route.name === "episodes" || route.name === "episode" || route.name === "editor";
  return <ShortcutsProvider><div className="shell">
    <header className="top">
      <Link to="/" className="brand" aria-label="CorteX, início"><span className="logo">CX</span><span className="brand-name">CorteX</span></Link>
      <nav className="nav" aria-label="Principal">
        <Link to="/" className={inEpisodes ? "on" : ""} aria-current={inEpisodes ? "page" : undefined}>Episódios</Link>
        <Link to="/cortes" className={route.name === "cuts" ? "on" : ""} aria-current={route.name === "cuts" ? "page" : undefined}>Cortes</Link>
      </nav>
      <div className="grow" />
      <div className="top-end"><ActivityPill /><ShortcutsButton /></div>
    </header>
    <main className="page"><ErrorBoundary resetKey={location.split("?")[0]}>{children}</ErrorBoundary></main>
  </div></ShortcutsProvider>;
}

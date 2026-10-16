import type { CSSProperties, ReactNode } from "react";
import { Link, type Route } from "../router";
import { Menu, MenuLabel } from "../ui";
import type { ApiJob } from "../api";
import { jobLabel } from "./humanize";
import { useActivity } from "./useActivity";
import "./shell.css";

const percent = (job: ApiJob) => Math.round(job.progress ?? 0);

function ActivityPill() {
  const { active, lead } = useActivity();
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

export function Shell({ route, children }: { route: Route; children: ReactNode }) {
  const inEpisodes = route.name === "episodes" || route.name === "episode" || route.name === "editor";
  return <div className="shell">
    <header className="top">
      <Link to="/" className="brand" aria-label="CorteX, início"><span className="logo">CX</span><span className="brand-name">CorteX</span></Link>
      <nav className="nav" aria-label="Principal">
        <Link to="/" className={inEpisodes ? "on" : ""} aria-current={inEpisodes ? "page" : undefined}>Episódios</Link>
        <Link to="/cortes" className={route.name === "cuts" ? "on" : ""} aria-current={route.name === "cuts" ? "page" : undefined}>Cortes</Link>
      </nav>
      <div className="grow" />
      <ActivityPill />
    </header>
    <main className="page">{children}</main>
  </div>;
}

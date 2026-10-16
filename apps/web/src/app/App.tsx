import { useEffect } from "react";
import { EditorPage } from "../features/editor/EditorPage";
import { EpisodePage } from "../features/episode/EpisodePage";
import { EpisodesPage } from "../features/episodes/EpisodesPage";
import { navigate, useRoute } from "../router";
import { EmptyState, ToastProvider } from "../ui";
import { Shell } from "./Shell";
import { usePageTitle } from "./usePageTitle";

function CutsPage() {
  usePageTitle("Cortes · CorteX");
  return <div className="stub"><EmptyState title="Em breve">A visão de todos os cortes exportados, com publicação e resultados, chega na próxima etapa.</EmptyState></div>;
}

export default function App() {
  const route = useRoute();
  usePageTitle(route.name === "episodes" || route.name === "unknown" ? "Episódios · CorteX" : null);
  useEffect(() => { if (route.name === "unknown") navigate("/", { replace: true }); }, [route.name]);

  return <ToastProvider>
    <Shell route={route}>
      {route.name === "episode" && <EpisodePage key={route.id} id={route.id} />}
      {route.name === "editor" && <EditorPage key={`${route.id}:${route.run}`} id={route.id} runId={route.run} clipKeys={route.clips} />}
      {route.name === "cuts" && <CutsPage />}
      {(route.name === "episodes" || route.name === "unknown") && <EpisodesPage />}
    </Shell>
  </ToastProvider>;
}

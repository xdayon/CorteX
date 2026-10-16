import { useEffect } from "react";
import { BoardPage } from "../features/board/BoardPage";
import { EditorPage } from "../features/editor/EditorPage";
import { EpisodePage } from "../features/episode/EpisodePage";
import { EpisodesPage } from "../features/episodes/EpisodesPage";
import { navigate, useRoute } from "../router";
import { ToastProvider } from "../ui";
import { Shell } from "./Shell";
import { usePageTitle } from "./usePageTitle";

export default function App() {
  const route = useRoute();
  usePageTitle(route.name === "episodes" || route.name === "unknown" ? "Episódios · CorteX" : null);
  useEffect(() => { if (route.name === "unknown") navigate("/", { replace: true }); }, [route.name]);

  return <ToastProvider>
    <Shell route={route}>
      {route.name === "episode" && <EpisodePage key={route.id} id={route.id} />}
      {route.name === "editor" && <EditorPage key={`${route.id}:${route.run}:${route.clips.join(",")}`} id={route.id} run={route.run} clipKeys={route.clips} />}
      {route.name === "cuts" && <BoardPage />}
      {(route.name === "episodes" || route.name === "unknown") && <EpisodesPage />}
    </Shell>
  </ToastProvider>;
}

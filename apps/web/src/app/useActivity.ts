import { useEffect, useState } from "react";
import { api, type ApiJob } from "../api";
import { newestActive } from "./humanize";

const BUSY_MS = 3000;
const IDLE_MS = 15000;
const OFFLINE_MS = 5000;

/** Polls the active jobs: quickly while something runs, slowly when idle; `offline` once a poll fails until the next one succeeds. */
export function useActivity() {
  const [jobs, setJobs] = useState<ApiJob[]>([]);
  const [offline, setOffline] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let timer = 0;
    async function tick() {
      let delay = IDLE_MS;
      try {
        const list = await api.activeJobs(controller.signal);
        if (controller.signal.aborted) return;
        const next = Array.isArray(list) ? list : [];
        setJobs(next);
        setOffline(false);
        if (newestActive(next).active.length) delay = BUSY_MS;
      } catch {
        if (controller.signal.aborted) return;
        setOffline(true);
        delay = OFFLINE_MS;
      }
      timer = window.setTimeout(() => void tick(), delay);
    }
    void tick();
    return () => { controller.abort(); window.clearTimeout(timer); };
  }, []);
  return { ...newestActive(jobs), offline };
}
